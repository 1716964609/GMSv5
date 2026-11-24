"""In-memory DynamoDB Table for tests. No AWS calls.

Evaluates the subset of DynamoDB expressions used by the availability Lambda,
so that conditional writes are tested as written rather than mocked away:

- Conditions: attribute_exists(), attribute_not_exists(), = <> < <= > >=,
  AND, OR, NOT and parentheses.
- Updates: SET a = v, SET a = if_not_exists(a, v), ADD set, REMOVE.
- Queries: Key(...).eq(...) combined with between / begins_with / >= etc.

Each operation is atomic (one lock), like a single DynamoDB item write.
"""
import copy
import re
import threading

from boto3.dynamodb.conditions import And, BeginsWith, Between, Equals
from boto3.dynamodb.conditions import GreaterThan, GreaterThanEquals, LessThan, LessThanEquals
from botocore.exceptions import ClientError

TOKEN = re.compile(r"\s*(<>|<=|>=|=|<|>|\(|\)|,|[#:]?[A-Za-z_][A-Za-z0-9_]*)")
MISSING = object()


def conditional_failure(operation):
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}},
        operation,
    )


def tokenize(text):
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match:
            raise ValueError(f"Unsupported expression near: {text[position:]}")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


class Condition:
    def __init__(self, text, names, values):
        self.tokens = tokenize(text)
        self.position = 0
        self.names = names or {}
        self.values = values or {}

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self, expected=None):
        token = self.peek()
        if expected is not None and (token or "").upper() != expected:
            raise ValueError(f"Expected {expected}, found {token}")
        self.position += 1
        return token

    def evaluate(self, item):
        self.item = item
        result = self.or_expression()
        if self.peek() is not None:
            raise ValueError(f"Unparsed token {self.peek()}")
        return result

    def or_expression(self):
        result = self.and_expression()
        while (self.peek() or "").upper() == "OR":
            self.take()
            right = self.and_expression()
            result = result or right
        return result

    def and_expression(self):
        result = self.not_expression()
        while (self.peek() or "").upper() == "AND":
            self.take()
            right = self.not_expression()
            result = result and right
        return result

    def not_expression(self):
        if (self.peek() or "").upper() == "NOT":
            self.take()
            return not self.not_expression()
        return self.primary()

    def primary(self):
        token = self.peek()
        if token == "(":
            self.take()
            result = self.or_expression()
            self.take(")")
            return result
        if token in ("attribute_exists", "attribute_not_exists"):
            self.take()
            self.take("(")
            name = self.path(self.take())
            self.take(")")
            exists = name in self.item
            return exists if token == "attribute_exists" else not exists
        left = self.operand(self.take())
        operator = self.take()
        right = self.operand(self.take())
        if left is MISSING or right is MISSING:
            return False
        if type(left) is not type(right) and not (
                isinstance(left, (int, float)) and isinstance(right, (int, float))):
            return operator == "<>"
        return {
            "=": left == right, "<>": left != right, "<": left < right,
            "<=": left <= right, ">": left > right, ">=": left >= right,
        }[operator]

    def path(self, token):
        return self.names[token] if token.startswith("#") else token

    def operand(self, token):
        if token.startswith(":"):
            return self.values[token]
        return self.item.get(self.path(token), MISSING)


def split_top_level(text):
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def apply_update(item, expression, names, values):
    names, values = names or {}, values or {}
    resolve = lambda token: names.get(token, token)  # noqa: E731
    clauses = re.split(r"\b(SET|ADD|REMOVE)\b", expression)
    action = None
    for chunk in clauses:
        chunk = chunk.strip()
        if chunk in ("SET", "ADD", "REMOVE"):
            action = chunk
            continue
        if not chunk:
            continue
        for part in split_top_level(chunk):
            if action == "SET":
                target, value = [piece.strip() for piece in part.split("=", 1)]
                match = re.fullmatch(r"if_not_exists\((\S+),\s*(:\w+)\)", value)
                name = resolve(target)
                if match:
                    existing = item.get(resolve(match.group(1)), MISSING)
                    item[name] = values[match.group(2)] if existing is MISSING else existing
                elif value.startswith(":"):
                    item[name] = values[value]
                else:
                    raise ValueError(f"Unsupported SET value {value}")
            elif action == "ADD":
                target, value = part.split()
                name = resolve(target)
                addition = values[value]
                if isinstance(addition, set):
                    item[name] = set(item.get(name, set())) | addition
                else:
                    item[name] = item.get(name, 0) + addition
            elif action == "REMOVE":
                item.pop(resolve(part), None)
            else:
                raise ValueError(f"Unsupported update clause {expression}")


def key_matches(condition, item):
    if isinstance(condition, And):
        return all(key_matches(part, item) for part in condition._values)
    attribute, *operands = condition._values
    value = item.get(attribute.name, MISSING)
    if value is MISSING:
        return False
    if isinstance(condition, Equals):
        return value == operands[0]
    if isinstance(condition, Between):
        return operands[0] <= value <= operands[1]
    if isinstance(condition, BeginsWith):
        return value.startswith(operands[0])
    if isinstance(condition, GreaterThanEquals):
        return value >= operands[0]
    if isinstance(condition, GreaterThan):
        return value > operands[0]
    if isinstance(condition, LessThanEquals):
        return value <= operands[0]
    if isinstance(condition, LessThan):
        return value < operands[0]
    raise ValueError(f"Unsupported key condition {condition!r}")


class FakeTable:
    def __init__(self, key_names=("instance_id", "event_time")):
        self.key_names = key_names
        self.items = {}
        self.lock = threading.Lock()
        self.calls = []

    def _key(self, mapping):
        return tuple(mapping[name] for name in self.key_names)

    @staticmethod
    def _validate_placeholders(expressions, names, values, operation):
        # Real DynamoDB rejects unused ExpressionAttributeNames / Values.
        text = " ".join(expression for expression in expressions if expression)
        used = set(re.findall(r"[#:][A-Za-z_][A-Za-z0-9_]*", text))
        unused = (set(names or {}) | set(values or {})) - used
        if unused:
            raise ClientError({"Error": {"Code": "ValidationException",
                                         "Message": f"unused {sorted(unused)}"}}, operation)

    def _check(self, item, condition, names, values, operation):
        if condition and not Condition(condition, names, values).evaluate(item):
            raise conditional_failure(operation)

    def get_item(self, *, Key, ConsistentRead=False):
        with self.lock:
            self.calls.append(("get_item", Key))
            item = self.items.get(self._key(Key))
            return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, *, Item, ConditionExpression=None,
                 ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        with self.lock:
            self.calls.append(("put_item", Item))
            self._validate_placeholders([ConditionExpression], ExpressionAttributeNames,
                                        ExpressionAttributeValues, "PutItem")
            key = self._key(Item)
            self._check(self.items.get(key, {}), ConditionExpression,
                        ExpressionAttributeNames, ExpressionAttributeValues, "PutItem")
            self.items[key] = copy.deepcopy(Item)
            return {}

    def update_item(self, *, Key, UpdateExpression, ConditionExpression=None,
                    ExpressionAttributeNames=None, ExpressionAttributeValues=None,
                    ReturnValues="NONE"):
        with self.lock:
            self.calls.append(("update_item", Key, UpdateExpression))
            self._validate_placeholders([UpdateExpression, ConditionExpression],
                                        ExpressionAttributeNames, ExpressionAttributeValues,
                                        "UpdateItem")
            key = self._key(Key)
            old = copy.deepcopy(self.items.get(key, {}))
            self._check(old, ConditionExpression, ExpressionAttributeNames,
                        ExpressionAttributeValues, "UpdateItem")
            new = copy.deepcopy(old) or dict(Key)
            apply_update(new, UpdateExpression, ExpressionAttributeNames,
                         ExpressionAttributeValues)
            self.items[key] = new
            if ReturnValues == "ALL_OLD":
                return {"Attributes": old}
            if ReturnValues == "ALL_NEW":
                return {"Attributes": copy.deepcopy(new)}
            return {}

    def query(self, *, KeyConditionExpression, ScanIndexForward=True, Limit=None,
              ConsistentRead=False):
        with self.lock:
            self.calls.append(("query",))
            rows = [copy.deepcopy(item) for item in self.items.values()
                    if key_matches(KeyConditionExpression, item)]
            rows.sort(key=lambda item: item[self.key_names[1]],
                      reverse=not ScanIndexForward)
            return {"Items": rows[:Limit] if Limit else rows}

    def row(self, partition, sort):
        return self.items.get((partition, sort))

    def partition(self, partition):
        return sorted((item for (pk, _), item in self.items.items() if pk == partition),
                      key=lambda item: item["event_time"])
