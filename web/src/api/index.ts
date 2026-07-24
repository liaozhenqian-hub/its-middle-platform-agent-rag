import { createApiClient } from "./client";

let csrfToken: string | null = null;

export const api = createApiClient(() => csrfToken);

export function setCsrfToken(value: string | null): void {
  csrfToken = value;
}
