import { useAuth } from "../auth/useAuth";

function MicrosoftIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 21 21" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="9" height="9" fill="#F25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7FBA00" />
      <rect x="1" y="11" width="9" height="9" fill="#00A4EF" />
      <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
    </svg>
  );
}

export default function LoginPage() {
  const { login } = useAuth();

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%)",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 16,
          boxShadow: "0 8px 40px rgba(0,0,0,0.12)",
          padding: "48px 40px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
          width: 340,
        }}
      >
        {/* Logo */}
        <img
          src="/LOgo.png"
          alt="Arvind Fashions"
          style={{ height: 48, objectFit: "contain" }}
          onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
        />

        {/* Title */}
        <div style={{ textAlign: "center" }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "#1a1a2e", margin: "0 0 6px" }}>
            Pulse AI
          </h1>
          <p style={{ fontSize: 13, color: "#6b7280", margin: 0 }}>
            Retail Analytics · Arvind Fashions
          </p>
        </div>

        <hr style={{ width: "100%", border: "none", borderTop: "1px solid #e5e7eb", margin: 0 }} />

        <p style={{ fontSize: 13, color: "#374151", margin: 0, textAlign: "center" }}>
          Sign in with your company account to access your personalised conversation history.
        </p>

        {/* Microsoft sign-in button */}
        <button
          onClick={login}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 10,
            background: "#2f2f2f",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "12px 20px",
            width: "100%",
            fontSize: 14,
            fontWeight: 600,
            cursor: "pointer",
            transition: "background 0.15s",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "#111"; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "#2f2f2f"; }}
        >
          <MicrosoftIcon />
          Sign in with Microsoft
        </button>

        <p style={{ fontSize: 11, color: "#9ca3af", margin: 0, textAlign: "center" }}>
          Use your @arvindfashions.com account
        </p>
      </div>
    </div>
  );
}
