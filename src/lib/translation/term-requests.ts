export type TermRequestToken = {
  scope: string;
  generation: number;
  signal: AbortSignal;
};

type ActiveRequest = {
  generation: number;
  controller: AbortController;
};

export class TermRequestCoordinator {
  private readonly active = new Map<string, ActiveRequest>();
  private readonly generations = new Map<string, number>();

  begin(scope: string): TermRequestToken {
    this.cancel(scope);
    const generation = (this.generations.get(scope) ?? 0) + 1;
    const controller = new AbortController();
    this.generations.set(scope, generation);
    this.active.set(scope, { generation, controller });
    return { scope, generation, signal: controller.signal };
  }

  isCurrent(token: TermRequestToken): boolean {
    return this.active.get(token.scope)?.generation === token.generation && !token.signal.aborted;
  }

  finish(token: TermRequestToken): void {
    if (this.isCurrent(token)) this.active.delete(token.scope);
  }

  cancel(scope: string): void {
    const request = this.active.get(scope);
    request?.controller.abort();
    this.active.delete(scope);
    this.generations.set(scope, (this.generations.get(scope) ?? 0) + 1);
  }

  cancelAll(): void {
    for (const scope of this.active.keys()) this.cancel(scope);
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
