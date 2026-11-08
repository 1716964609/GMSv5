export function aborted(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}

type WaitingRequest = { start: () => void; cancel: () => void };

/** FIFO semaphore; a permit is held until fetch and body processing settle. */
export class TermFetchScheduler {
  private active = 0;
  private readonly waiting: WaitingRequest[] = [];
  private readonly limit: number;

  constructor(limit = 4) {
    if (!Number.isInteger(limit) || limit < 1 || limit > 4) throw new Error("Invalid Term request limit");
    this.limit = limit;
  }

  run<T>(signal: AbortSignal, task: () => Promise<T>): Promise<T> {
    if (signal.aborted) return Promise.reject(aborted());
    return new Promise<T>((resolve, reject) => {
      const entry: WaitingRequest = {
        cancel: () => {
          const index = this.waiting.indexOf(entry);
          if (index !== -1) {
            this.waiting.splice(index, 1);
            signal.removeEventListener("abort", entry.cancel);
            reject(aborted());
          }
        },
        start: () => {
          signal.removeEventListener("abort", entry.cancel);
          this.active += 1;
          void (async () => {
            try {
              if (signal.aborted) throw aborted();
              const value = await task();
              if (signal.aborted) throw aborted();
              resolve(value);
            } catch (error) {
              reject(error);
            } finally {
              this.active -= 1;
              this.drain();
            }
          })();
        },
      };
      signal.addEventListener("abort", entry.cancel, { once: true });
      this.waiting.push(entry);
      this.drain();
    });
  }

  private drain(): void {
    while (this.active < this.limit && this.waiting.length > 0) this.waiting.shift()!.start();
  }
}

const tabSchedulerKey = Symbol.for("gms.termFetchScheduler");

/** Browser lifetime singleton. No request scheduler is shared between SSR users. */
export function getTabTermScheduler(): TermFetchScheduler {
  if (typeof window === "undefined") throw new Error("Term scheduler requires a browser tab");
  const tab = window as unknown as Record<symbol, TermFetchScheduler | undefined>;
  tab[tabSchedulerKey] ??= new TermFetchScheduler(4);
  return tab[tabSchedulerKey];
}

export type TermFetchConcurrency = 1 | 2 | 4;

export function termFetchConcurrency(value: string | undefined): TermFetchConcurrency {
  return value === "1" ? 1 : value === "4" ? 4 : 2;
}
