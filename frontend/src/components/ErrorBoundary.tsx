import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { hasError: boolean };

/** App/page error boundary — no stack traces in production UI. */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Intentionally no console dump of PII; host monitoring may attach later.
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="card empty-state" style={{ margin: "2rem auto", maxWidth: 420 }}>
          <h3>Something went wrong</h3>
          <p>An unexpected error occurred. You can reload this page and continue.</p>
          <div style={{ marginTop: "1rem" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                this.setState({ hasError: false });
                window.location.reload();
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
