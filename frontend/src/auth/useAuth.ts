import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { loginRequest } from "./msalConfig";

/**
 * Convenience hook — exposes the current user's name, email, login/logout,
 * and a getToken() function that returns the ID token for backend API calls.
 */
export function useAuth() {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = accounts[0] ?? null;

  /** Silently refresh and return the ID token (used as Bearer on API calls). */
  async function getToken(): Promise<string> {
    if (!account) throw new Error("No authenticated account");
    const resp = await instance.acquireTokenSilent({
      ...loginRequest,
      account,
    });
    if (!resp.idToken) {
      throw new Error("No ID token available");
    }
    return resp.idToken;
  }

  function login() {
    instance.loginRedirect(loginRequest);
  }

  function logout() {
    instance.logoutRedirect({ account });
  }

  return {
    isAuthenticated,
    account,
    /** Display name (e.g. "John Doe") */
    name: account?.name ?? "",
    /** UPN / email (e.g. "john.doe@company.com") */
    email: account?.username ?? "",
    getToken,
    login,
    logout,
  };
}
