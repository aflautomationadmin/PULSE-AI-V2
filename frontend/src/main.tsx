import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { msalInstance } from './auth/msalConfig.ts'

// MSAL v3+ requires explicit async initialization before the app renders.
// Without this the MsalProvider throws and the page is blank.
msalInstance.initialize().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
