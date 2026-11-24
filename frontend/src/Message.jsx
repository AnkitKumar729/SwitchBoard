// The data-testid values are part of the assignment's UI contract. Each meta-* element holds the
// bare value only, so a test can read it directly.

export function UserMessage({ content }) {
  return (
    <div className="message user" data-testid="user-message">
      {content}
    </div>
  );
}

export function AssistantMessage({ content, metadata }) {
  const cached = metadata.resolution_source === "LLM_CACHE";
  const failed = metadata.fallback_attempts; // keys that failed before this one answered
  return (
    <article className="message assistant" data-testid="assistant-message">
      <p className="reply">{content}</p>
      <dl className="meta">
        <div className="meta-cell">
          <dt>Source</dt>
          <dd>
            <span className={cached ? "chip chip-cache" : "chip chip-live"} data-testid="meta-source">
              {metadata.resolution_source}
            </span>
          </dd>
        </div>
        <div className="meta-cell meta-cell-wide">
          <dt>Model</dt>
          <dd data-testid="meta-model">{metadata.model}</dd>
        </div>
        <div className="meta-cell">
          <dt>Latency</dt>
          <dd data-testid="meta-latency">{`${metadata.latency_ms} ms`}</dd>
        </div>
        <div className="meta-cell">
          <dt>Input tokens</dt>
          <dd data-testid="meta-input-tokens">{metadata.input_tokens}</dd>
        </div>
        <div className="meta-cell">
          <dt>Output tokens</dt>
          <dd data-testid="meta-output-tokens">{metadata.output_tokens}</dd>
        </div>
      </dl>
      {cached ? (
        <p className="route">Served from the cache, so no API key was used.</p>
      ) : (
        <p className={failed ? "route route-failover" : "route"}>
          Answered by <b data-testid="meta-key">{metadata.key_id}</b>
          {failed ? (
            <>
              {" after "}
              <b data-testid="meta-fallbacks">{failed}</b>
              {failed === 1 ? " key failed." : " keys failed."}
            </>
          ) : (
            " on the first attempt."
          )}
        </p>
      )}
    </article>
  );
}

export function ErrorMessage({ status, content }) {
  return (
    <div className="message error" role="alert" data-testid="error-message">
      <span className="error-badge" aria-hidden="true">
        !
      </span>
      <div>
        <p className="error-title">{status ? `Request failed · ${status}` : "Request failed"}</p>
        <p>{content}</p>
      </div>
    </div>
  );
}
