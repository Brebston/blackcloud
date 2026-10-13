// Межа помилок: збій рендерингу одного фрагмента (діалогу, панелі) не «вибілює» весь застосунок.
// fallback — вузол або функція (error, reset); resetKey — зміна значення скидає помилку.
import { Component, ReactNode } from "react";
import { tr } from "../i18n";

interface Props {
  children?: ReactNode;
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  resetKey?: unknown;
}

interface State {
  error: Error | null;
  key: unknown;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  componentDidCatch(error: Error) {
    console.error("UI error", error);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children ?? null;
    const { fallback } = this.props;
    if (typeof fallback === "function") return fallback(error, this.reset);
    if (fallback !== undefined) return fallback;
    return (
      <div className="banner banner-danger" role="alert">
        {tr("mail2.crashed")}{" "}
        <button type="button" className="link-btn" onClick={this.reset}>
          {tr("mail2.retry")}
        </button>
      </div>
    );
  }
}
