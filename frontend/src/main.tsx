// Entry point: loads Bootstrap and the app styles, then renders <App /> into #root.

import 'bootstrap/dist/css/bootstrap.min.css'
import './styles/app.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
