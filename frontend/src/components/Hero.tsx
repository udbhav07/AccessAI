// The title banner at the top of the page. Decorative only.

export function Hero() {
  return (
    <header className="hero text-center">
      <h1 className="hero-title display-1 fw-semibold text-white mb-2">
        <CodeIcon />
        {/* single flex item so the gap doesn't split the word */}
        <span>
          Access<span className="text-black">AI</span>
        </span>
        <CodeSlashIcon />
      </h1>
      <p className="typewriter display-6 fw-bold mb-0">
        Enhance <span className="text-white">Viewer</span> Experience.
      </p>
      <svg
        className="hero-wave"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 1440 130"
        aria-hidden="true"
        focusable="false"
      >
        <path
          fill="var(--bs-body-bg)"
          d="M0,20L34.3,36C68.6,52,137,84,206,94.7C274.3,105,343,95,411,78.7C480,63,549,41,617,46.7C685.7,52,754,84,823,78.7C891.4,73,960,31,1029,25.3C1097.1,20,1166,52,1234,62.7C1302.9,73,1371,63,1406,57.3L1440,52L1440,320L0,320Z"
        />
      </svg>
    </header>
  )
}

function CodeIcon() {
  return (
    <svg width="1.2em" height="1.2em" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M5.854 4.854a.5.5 0 1 0-.708-.708l-3.5 3.5a.5.5 0 0 0 0 .708l3.5 3.5a.5.5 0 0 0 .708-.708L2.707 8zm4.292 0a.5.5 0 0 1 .708-.708l3.5 3.5a.5.5 0 0 1 0 .708l-3.5 3.5a.5.5 0 0 1-.708-.708L13.293 8z" />
    </svg>
  )
}

function CodeSlashIcon() {
  return (
    <svg width="1.2em" height="1.2em" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M10.478 1.647a.5.5 0 1 0-.956-.294l-4 13a.5.5 0 0 0 .956.294zM4.854 4.146a.5.5 0 0 1 0 .708L1.707 8l3.147 3.146a.5.5 0 0 1-.708.708l-3.5-3.5a.5.5 0 0 1 0-.708l3.5-3.5a.5.5 0 0 1 .708 0m6.292 0a.5.5 0 0 0 0 .708L14.293 8l-3.147 3.146a.5.5 0 0 0 .708.708l3.5-3.5a.5.5 0 0 0 0-.708l-3.5-3.5a.5.5 0 0 0-.708 0" />
    </svg>
  )
}
