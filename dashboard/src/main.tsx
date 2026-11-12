import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import RemoteViewer from './RemoteViewer.tsx'

const Application = location.pathname.startsWith('/remote') ? RemoteViewer : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Application />
  </StrictMode>,
)
