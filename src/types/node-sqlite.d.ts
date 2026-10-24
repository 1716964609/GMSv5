declare module "node:sqlite" {
  export interface StatementSync {
    run(...parameters: unknown[]): unknown;
    get<T extends Record<string, unknown> = Record<string, unknown>>(
      ...parameters: unknown[]
    ): T | undefined;
    all<T extends Record<string, unknown> = Record<string, unknown>>(
      ...parameters: unknown[]
    ): T[];
  }

  export class DatabaseSync {
    constructor(location: string);
    exec(sql: string): void;
    prepare(sql: string): StatementSync;
    close(): void;
  }
}
