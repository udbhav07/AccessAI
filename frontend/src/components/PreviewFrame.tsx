// Shows the fixed page in a fully sandboxed iframe, so the scanned page can't run anything.

export function PreviewFrame({ html }: { html: string }) {
  return (
    <section className="preview mt-4" aria-labelledby="preview-heading">
      <h2 id="preview-heading" className="h3 pb-2">
        Output
      </h2>
      {/* Keep the sandbox empty. srcdoc inherits our origin, so allow-same-origin
          would give the scraped page this app's origin. */}
      <iframe
        srcDoc={html}
        sandbox=""
        referrerPolicy="no-referrer"
        title="Remediated page preview"
        className="preview-frame"
      />
    </section>
  )
}
