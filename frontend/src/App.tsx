// The whole page. Takes server status from useAppConfig and scan state from useScan,
// and decides which notices and result sections to show.

import { ChangesSummary } from './components/ChangesSummary'
import { Hero } from './components/Hero'
import { AiDownNotice, ErrorNotice, ScanWarnings, ServerDownNotice } from './components/Notices'
import { PreviewFrame } from './components/PreviewFrame'
import { ResultActions } from './components/ResultActions'
import { ScanForm } from './components/ScanForm'
import { Spinner } from './components/Spinner'
import { VerificationReport } from './components/VerificationReport'
import { useAppConfig } from './hooks/useAppConfig'
import { useScan } from './hooks/useScan'

export default function App() {
  const { config, error: configError } = useAppConfig()
  const { scan, phase, error, startScan, verify, download, dismissError } = useScan()
  const busy = phase !== 'idle'
  const aiDown = config?.ai_enabled === false || scan?.ai_fallback === true

  return (
    <>
      <Hero />
      <main className="container pb-5">
        <div className="row justify-content-center">
          <div className="col-12 col-lg-8">
            <ScanForm busy={busy} scanning={phase === 'scanning'} onScan={startScan} />
            {configError && <ServerDownNotice message={configError} />}
            {aiDown && <AiDownNotice />}
            {error && <ErrorNotice message={error} onDismiss={dismissError} />}
          </div>
        </div>

        {phase === 'restoring' && (
          <p className="text-center mt-4" role="status">
            <Spinner />
            Loading the previous result…
          </p>
        )}

        {scan && (
          <>
            <ResultActions
              scan={scan}
              busy={busy}
              verifying={phase === 'verifying'}
              onVerify={verify}
              onDownload={download}
            />
            <ScanWarnings warnings={scan.warnings ?? []} />
            {scan.report && <VerificationReport report={scan.report} />}
            <ChangesSummary issues={scan.issues} />
            <PreviewFrame html={scan.html} />
          </>
        )}
      </main>
    </>
  )
}
