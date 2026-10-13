"""Theme the interactive API reference without changing its Swagger UI setup."""

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

_THEME_HEAD = """
<script>
  // Apply the saved choice before Swagger UI renders to avoid a light flash.
  (() => {
    const preference = window.matchMedia('(prefers-color-scheme: dark)').matches;
    try {
      const saved = window.localStorage.getItem('ground-station-api-docs-theme');
      document.documentElement.dataset.docsTheme =
        saved === 'light' || saved === 'dark' ? saved : (preference ? 'dark' : 'light');
    } catch (_) {
      document.documentElement.dataset.docsTheme = preference ? 'dark' : 'light';
    }
  })();
</script>
<style>
  :root {
    color-scheme: light;
    --docs-bg: #ffffff;
    --docs-surface: #ffffff;
    --docs-text: #202a35;
    --docs-muted: #526171;
    --docs-border: #d8dee5;
    --docs-field: #ffffff;
    --docs-code: #f4f6f8;
  }
  :root[data-docs-theme="dark"] {
    color-scheme: dark;
    --docs-bg: #101820;
    --docs-surface: #1b2732;
    --docs-text: #e8eef5;
    --docs-muted: #b6c4d2;
    --docs-border: #40505e;
    --docs-field: #202f3b;
    --docs-code: #16212b;
  }
  body { margin: 0; background: var(--docs-bg); }
  .docs-toolbar {
    align-items: center;
    background: var(--docs-surface);
    border-bottom: 1px solid var(--docs-border);
    display: flex;
    justify-content: flex-end;
    padding: 8px max(16px, calc((100vw - 1460px) / 2));
  }
  .docs-theme-toggle {
    background: var(--docs-field);
    border: 1px solid var(--docs-border);
    border-radius: 5px;
    color: var(--docs-text);
    cursor: pointer;
    font: 600 13px/1.4 system-ui, sans-serif;
    padding: 7px 12px;
  }
  .docs-theme-toggle:hover, .docs-theme-toggle:focus-visible {
    border-color: #499bd2;
    outline: 2px solid transparent;
  }
  .docs-theme-toggle:focus-visible { box-shadow: 0 0 0 2px #499bd2; }

  :root[data-docs-theme="dark"] .swagger-ui,
  :root[data-docs-theme="dark"] .swagger-ui .info .title,
  :root[data-docs-theme="dark"] .swagger-ui .info p,
  :root[data-docs-theme="dark"] .swagger-ui .info li,
  :root[data-docs-theme="dark"] .swagger-ui .info h1,
  :root[data-docs-theme="dark"] .swagger-ui .info h2,
  :root[data-docs-theme="dark"] .swagger-ui .info h3,
  :root[data-docs-theme="dark"] .swagger-ui .opblock-tag,
  :root[data-docs-theme="dark"] .swagger-ui .opblock-summary-description,
  :root[data-docs-theme="dark"] .swagger-ui .opblock-description-wrapper p,
  :root[data-docs-theme="dark"] .swagger-ui .markdown p,
  :root[data-docs-theme="dark"] .swagger-ui .markdown li,
  :root[data-docs-theme="dark"] .swagger-ui .renderedMarkdown p,
  :root[data-docs-theme="dark"] .swagger-ui .renderedMarkdown li,
  :root[data-docs-theme="dark"] .swagger-ui .opblock-section-header h4,
  :root[data-docs-theme="dark"] .swagger-ui .response-col_status,
  :root[data-docs-theme="dark"] .swagger-ui .response-col_description,
  :root[data-docs-theme="dark"] .swagger-ui .model-title,
  :root[data-docs-theme="dark"] .swagger-ui .model,
  :root[data-docs-theme="dark"] .swagger-ui .prop-type,
  :root[data-docs-theme="dark"] .swagger-ui .parameter__name,
  :root[data-docs-theme="dark"] .swagger-ui .parameter__type,
  :root[data-docs-theme="dark"] .swagger-ui table thead tr th,
  :root[data-docs-theme="dark"] .swagger-ui table tbody tr td,
  :root[data-docs-theme="dark"] .swagger-ui label,
  :root[data-docs-theme="dark"] .swagger-ui .tab li,
  :root[data-docs-theme="dark"] .swagger-ui .dialog-ux .modal-ux-header h3 {
    color: var(--docs-text);
  }
  :root[data-docs-theme="dark"] .swagger-ui .info .base-url,
  :root[data-docs-theme="dark"] .swagger-ui .info .title small,
  :root[data-docs-theme="dark"] .swagger-ui .parameter__in,
  :root[data-docs-theme="dark"] .swagger-ui .parameter__deprecated,
  :root[data-docs-theme="dark"] .swagger-ui .response-col_links {
    color: var(--docs-muted);
  }
  :root[data-docs-theme="dark"] .swagger-ui a { color: #80c7f2; }
  :root[data-docs-theme="dark"] .swagger-ui .scheme-container,
  :root[data-docs-theme="dark"] .swagger-ui .opblock-section-header,
  :root[data-docs-theme="dark"] .swagger-ui .models,
  :root[data-docs-theme="dark"] .swagger-ui .model-container,
  :root[data-docs-theme="dark"] .swagger-ui .dialog-ux .modal-ux,
  :root[data-docs-theme="dark"] .swagger-ui .dialog-ux .modal-ux-header,
  :root[data-docs-theme="dark"] .swagger-ui .dialog-ux .modal-ux-content,
  :root[data-docs-theme="dark"] .swagger-ui .auth-container {
    background: var(--docs-surface);
    color: var(--docs-text);
    box-shadow: none;
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock-tag,
  :root[data-docs-theme="dark"] .swagger-ui .models,
  :root[data-docs-theme="dark"] .swagger-ui .model-container,
  :root[data-docs-theme="dark"] .swagger-ui .dialog-ux .modal-ux-header,
  :root[data-docs-theme="dark"] .swagger-ui table thead tr td,
  :root[data-docs-theme="dark"] .swagger-ui table thead tr th {
    border-color: var(--docs-border);
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock.opblock-get {
    background: #142d3b;
    border-color: #4f9cc1;
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock.opblock-post {
    background: #173127;
    border-color: #56a779;
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock.opblock-put,
  :root[data-docs-theme="dark"] .swagger-ui .opblock.opblock-patch {
    background: #342b1c;
    border-color: #bf9651;
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock.opblock-delete {
    background: #382225;
    border-color: #c6777e;
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock .opblock-summary,
  :root[data-docs-theme="dark"] .swagger-ui .opblock .opblock-section-header {
    border-color: var(--docs-border);
  }
  :root[data-docs-theme="dark"] .swagger-ui .opblock .opblock-summary-path,
  :root[data-docs-theme="dark"] .swagger-ui .opblock .opblock-summary-path__deprecated,
  :root[data-docs-theme="dark"] .swagger-ui .opblock .opblock-summary-description {
    color: var(--docs-text);
  }
  :root[data-docs-theme="dark"] .swagger-ui input,
  :root[data-docs-theme="dark"] .swagger-ui select,
  :root[data-docs-theme="dark"] .swagger-ui textarea,
  :root[data-docs-theme="dark"] .swagger-ui .btn {
    background: var(--docs-field);
    border-color: var(--docs-border);
    color: var(--docs-text);
  }
  :root[data-docs-theme="dark"] .swagger-ui input::placeholder,
  :root[data-docs-theme="dark"] .swagger-ui textarea::placeholder {
    color: var(--docs-muted);
  }
  :root[data-docs-theme="dark"] .swagger-ui .btn.execute {
    background: #237bb2;
    border-color: #237bb2;
    color: #ffffff;
  }
  :root[data-docs-theme="dark"] .swagger-ui .btn.authorize {
    background: transparent;
    border-color: #65b781;
    color: #91d9a9;
  }
  :root[data-docs-theme="dark"] .swagger-ui .highlight-code,
  :root[data-docs-theme="dark"] .swagger-ui .microlight,
  :root[data-docs-theme="dark"] .swagger-ui .model-example,
  :root[data-docs-theme="dark"] .swagger-ui .response-col_description pre,
  :root[data-docs-theme="dark"] .swagger-ui .renderedMarkdown code {
    background: var(--docs-code) !important;
    color: var(--docs-text) !important;
  }
  :root[data-docs-theme="dark"] .swagger-ui .highlight-code *,
  :root[data-docs-theme="dark"] .swagger-ui .microlight * {
    color: var(--docs-text) !important;
  }
  :root[data-docs-theme="dark"] .swagger-ui svg.arrow,
  :root[data-docs-theme="dark"] .swagger-ui svg.unlocked,
  :root[data-docs-theme="dark"] .swagger-ui svg.locked,
  :root[data-docs-theme="dark"] .swagger-ui .model-toggle {
    filter: invert(1);
  }
</style>
"""

_THEME_CONTROLS = """
<div class="docs-toolbar">
  <button id="docs-theme-toggle" class="docs-theme-toggle" type="button"
          aria-label="Dark mode" aria-pressed="false">Dark mode</button>
</div>
"""

_THEME_SCRIPT = """
<script>
  (() => {
    const button = document.getElementById('docs-theme-toggle');
    const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
    const updateButton = () => {
      button.setAttribute('aria-pressed', String(document.documentElement.dataset.docsTheme === 'dark'));
    };
    button.addEventListener('click', () => {
      const next = document.documentElement.dataset.docsTheme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.docsTheme = next;
      try { window.localStorage.setItem('ground-station-api-docs-theme', next); } catch (_) {}
      updateButton();
    });
    // Follow OS changes until the visitor explicitly chooses a theme.
    systemTheme.addEventListener('change', (event) => {
      try {
        const saved = window.localStorage.getItem('ground-station-api-docs-theme');
        if (saved === 'light' || saved === 'dark') return;
      } catch (_) {}
      document.documentElement.dataset.docsTheme = event.matches ? 'dark' : 'light';
      updateButton();
    });
    updateButton();
  })();
</script>
"""


def get_themed_swagger_ui_html(openapi_url: str, title: str) -> HTMLResponse:
    """Keep FastAPI's Swagger initialization and add only theme UI around it."""
    html = get_swagger_ui_html(openapi_url=openapi_url, title=title).body.decode("utf-8")
    html = html.replace("</head>", f"{_THEME_HEAD}</head>", 1)
    html = html.replace('<div id="swagger-ui">', f'{_THEME_CONTROLS}<div id="swagger-ui">', 1)
    html = html.replace("</body>", f"{_THEME_SCRIPT}</body>", 1)
    return HTMLResponse(html)
