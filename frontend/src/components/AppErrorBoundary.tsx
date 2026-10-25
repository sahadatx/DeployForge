import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { hasError: boolean };

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the diagnostic local to the browser console; never expose stack traces in the UI.
    console.error("DeployForge interface rendering failed", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-error-screen" role="alert">
          <div className="app-error-card">
            <span className="app-error-mark" aria-hidden="true">DF</span>
            <p className="eyebrow">DEPLOYFORGE CONTROL PLANE</p>
            <h1>This page could not be displayed</h1>
            <p>Reload the workspace to restore the interface. Your platform data remains stored by the API.</p>
            <button type="button" onClick={() => window.location.reload()}>Reload workspace</button>
          </div>
        </main>
      );
    }

    return this.props.children;
  }
}
