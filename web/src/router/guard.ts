export interface GuardAuth {
  initialized: boolean;
  authenticated: boolean;
  restore(): Promise<boolean>;
}

interface GuardRoute {
  fullPath: string;
  meta: Record<string, unknown>;
}

export function createAdminGuard(auth: GuardAuth) {
  return async (to: GuardRoute) => {
    if (!to.meta.requiresAdmin) return true;
    if (!auth.initialized) await auth.restore();
    if (auth.authenticated) return true;
    return { name: "admin-login", query: { redirect: to.fullPath } };
  };
}
