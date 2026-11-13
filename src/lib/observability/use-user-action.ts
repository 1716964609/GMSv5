"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isRumActive, type UserAction } from "./rum-client.ts";
import type { RumActionName, RumActionOutcome } from "./schema.ts";

/**
 * Returns `complete(action, outcome)`, which ends a RUM user action after
 * React has committed the render that shows the result and the browser has
 * painted it ("click → rendered result", As-To-Be §5.1).
 *
 * The internal tick update is batched by React with the result state
 * updates made in the same handler, so the effect runs after that commit.
 * When RUM is inactive this does nothing and causes no extra render.
 */
export function useUserActionCompletion() {
  const pending = useRef<Array<() => void>>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    for (const finish of pending.current.splice(0)) {
      finish();
    }
  }, [tick]);

  return useCallback(
    <N extends RumActionName>(
      action: UserAction<N>,
      outcome: RumActionOutcome<N>,
      details?: { httpStatus?: number },
    ) => {
      if (!isRumActive()) {
        return;
      }
      pending.current.push(() => action.endAfterPaint(outcome, details));
      setTick((value) => value + 1);
    },
    [],
  );
}
