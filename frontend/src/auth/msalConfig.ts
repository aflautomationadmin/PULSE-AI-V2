import { type Configuration, PublicClientApplication, LogLevel } from "@azure/msal-browser";

export const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID as string,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID as string}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage",
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        if (level === LogLevel.Error) console.error("[MSAL]", message);
        if (level === LogLevel.Warning) console.warn("[MSAL]", message);
      },
      logLevel: LogLevel.Warning,
    },
  },
};

// Must call msalInstance.initialize() before rendering — done in main.tsx
export const msalInstance = new PublicClientApplication(msalConfig);

/**
 * Scopes for the silent token request.
 * We use OIDC scopes only — the returned ID token is sent as the Bearer value.
 * ID tokens always carry aud == clientId, making backend validation simple.
 */
export const loginRequest = {
  scopes: ["openid", "profile", "email"],
};
