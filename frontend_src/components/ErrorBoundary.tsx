import { Component, type ReactNode } from "react";
import { t } from "../i18n";

/** Isolate a failed view so navigation and administrator recovery stay usable. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="card">
      <p className="banner banner-error" role="alert">{t("shell.renderFailed")}</p>
      <button className="btn btn-outline" onClick={() => this.setState({ failed: false })}>{t("common.refresh")}</button>
    </div>;
  }
}
