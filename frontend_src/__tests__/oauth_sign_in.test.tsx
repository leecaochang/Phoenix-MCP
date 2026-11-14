/** OAuth sign-in in the panel: the switch, the sign-in window's countdown and its ends, and the approval dialog. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({
  api: {
    getAccessKeyOAuth: vi.fn(), patchAccessKey: vi.fn(), openOAuthWindow: vi.fn(), closeOAuthWindow: vi.fn(), removeOAuthSignIn: vi.fn(),
    getOAuthRequest: vi.fn(), approveOAuthRequest: vi.fn(), denyOAuthRequest: vi.fn(), getOAuthSummary: vi.fn(),
  },
}));
import { api } from "../api";
import { AccessKeyOAuth, countdown } from "../components/AccessKeyOAuth";
import { OAuthRequestDialog } from "../components/OAuthRequestDialog";
import type { AccessKeyOAuthStatus, AccessKeyRecord, OAuthSignInRequest } from "../types";

const NOW = Date.parse("2026-11-10T12:00:00Z");
const SIGN_IN = {
  id: "s1", client_id: "https://app.example/c.json", client_name: "Test App", redirect_host: "app.example",
  created_at: "2026-11-10T11:00:00Z", created_by: "u", last_used_at: "2026-11-10T11:30:00Z",
};

function status(over: Partial<AccessKeyOAuthStatus> = {}): AccessKeyOAuthStatus {
  return {
    oauth_enabled: true, window_open_until: null, kill_switch: false,
    public_url: "https://phx.example/api/phoenix-mcp", max_sign_ins: 10, sign_ins: [], ...over,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: false, toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
  vi.setSystemTime(NOW);
  // The panel is normally opened at its HTTPS address; the test page is plain HTTP.
  const page = window.location;
  vi.spyOn(window, "location", "get").mockReturnValue({ ...page, protocol: "https:", pathname: page.pathname, assign: page.assign } as Location);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

it("formats the time left as minutes and seconds", () => {
  expect(countdown(600_000)).toBe("10:00");
  expect(countdown(61_500)).toBe("1:02");
  expect(countdown(-5)).toBe("0:00");
});

it("turns sign-in on and shows the address and the countdown", async () => {
  vi.mocked(api.getAccessKeyOAuth)
    .mockResolvedValueOnce(status({ oauth_enabled: false }))
    .mockResolvedValue(status({ window_open_until: new Date(NOW + 600_000).toISOString() }));
  vi.mocked(api.patchAccessKey).mockResolvedValue({ id: "k1", oauth_enabled: true } as AccessKeyRecord);
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  const toggle = screen.getByRole("checkbox", { name: "Allow OAuth sign-in" });
  expect(toggle).not.toBeChecked();
  await act(async () => { fireEvent.click(toggle); });
  await settle();
  expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { oauth_enabled: true });
  expect(screen.getByText("https://phx.example/api/phoenix-mcp")).toBeInTheDocument();
  expect(screen.getByText("Sign-in open for 10:00")).toBeInTheDocument();
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(screen.getByText("Sign-in open for 9:59")).toBeInTheDocument();
});

it("says which app signed in when an approval closes the window", async () => {
  vi.mocked(api.getAccessKeyOAuth)
    .mockResolvedValueOnce(status({ window_open_until: new Date(NOW + 600_000).toISOString() }))
    .mockResolvedValue(status({ sign_ins: [SIGN_IN] }));
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  await act(async () => { vi.advanceTimersByTime(3000); });
  await settle();
  expect(screen.getByText("Signed in: Test App")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Allow new sign-in" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove the sign-in of Test App" })).toBeInTheDocument();
});

it("offers the window again, without a message, when the time ran out with no sign-in", async () => {
  vi.mocked(api.getAccessKeyOAuth)
    .mockResolvedValueOnce(status({ window_open_until: new Date(NOW + 2000).toISOString() }))
    .mockResolvedValue(status());
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  await act(async () => { vi.advanceTimersByTime(2000); });
  await settle();
  expect(screen.queryByText(/Sign-in open for/)).toBeNull();
  expect(screen.queryByText(/period ended|Signed in:/)).toBeNull();
  const reopened = status({ window_open_until: new Date(Date.now() + 600_000).toISOString() });
  vi.mocked(api.openOAuthWindow).mockResolvedValue(reopened);
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(reopened);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Allow new sign-in" })); });
  await settle();
  expect(api.openOAuthWindow).toHaveBeenCalledWith("k1");
  expect(screen.getByText("Sign-in open for 10:00")).toBeInTheDocument();
});

it("shows the kill switch stopping sign-in and keeps the switch from turning on", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ oauth_enabled: false, kill_switch: true }));
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  expect(screen.getByRole("checkbox", { name: "Allow OAuth sign-in" })).toBeDisabled();

  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ kill_switch: true }));
  render(<AccessKeyOAuth accessKeyId="k2" />);
  await settle();
  expect(screen.getByText("Stopped by the kill switch.")).toBeInTheDocument();
});

it("asks before switching off a key with signed-in apps and says how many", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ sign_ins: [SIGN_IN, { ...SIGN_IN, id: "s2" }] }));
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  await act(async () => { fireEvent.click(screen.getByRole("checkbox", { name: "Allow OAuth sign-in" })); });
  expect(api.patchAccessKey).not.toHaveBeenCalled();
  expect(screen.getByText("This also signs out 2 apps that use OAuth.")).toBeInTheDocument();
});

const REQUEST: OAuthSignInRequest = {
  id: "r1", client_id: "https://app.example/c.json", client_host: "app.example", client_name: "Test App",
  verified: true, problem: null, redirect_host: "app.example", loopback_only: false,
  created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 600_000).toISOString(),
  keys: [{ id: "k1", name: "phone", persona: "custom", pass_through: false, window_open_until: new Date(NOW + 600_000).toISOString(), at_cap: false }],
};

it("approves for the one open key and returns the browser to the app", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue(REQUEST);
  vi.mocked(api.approveOAuthRequest).mockResolvedValue({ redirect_to: "https://app.example/cb?code=x" });
  const assign = vi.fn();
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign } as Location);
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.getByText("Test App, as the app names itself")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "phone" })).toBeChecked();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Approve" })); });
  await settle();
  expect(api.approveOAuthRequest).toHaveBeenCalledWith("r1", "k1");
  expect(assign).toHaveBeenCalledWith("https://app.example/cb?code=x");
});

it("offers no approval for an app it could not verify", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({ ...REQUEST, verified: false, problem: "oauthRedirectNotListed" });
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByRole("alert")).toHaveTextContent("doesn't list the address");
});

it("disables approval once the request has run out", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({ ...REQUEST, expires_at: new Date(NOW + 1000).toISOString() });
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getAllByText("This sign-in request has ended. Start again from the app.").length).toBeGreaterThan(0);
});

it("names the app by its full client address", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue(REQUEST);
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.getByText("https://app.example/c.json")).toBeInTheDocument();
});

it("tells a screen reader the end time once and warns once near the end", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({ ...REQUEST, expires_at: new Date(NOW + 62_000).toISOString() });
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.getByText(/^This request ends at /)).toBeInTheDocument();
  const status = screen.getAllByRole("status").find((el) => el.classList.contains("sr-only")) as HTMLElement;
  expect(status).toHaveTextContent("");
  await act(async () => { vi.advanceTimersByTime(3000); });
  expect(status).toHaveTextContent("Less than a minute is left to approve this request.");
  await act(async () => { vi.advanceTimersByTime(60_000); });
  expect(status).toHaveTextContent("This sign-in request has ended. Start again from the app.");
});

it("says no window is open when the link names nothing and no key is open", async () => {
  vi.mocked(api.getOAuthRequest).mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
  vi.mocked(api.getOAuthSummary).mockResolvedValue({ keys: { k1: { sign_ins: 0, window_open_until: null } }, kill_switch: false });
  render(<OAuthRequestDialog link="abc" onClose={() => {}} />);
  await settle();
  await settle();
  expect(screen.getByText(/accepts a new sign-in right now/)).toBeInTheDocument();
});

it("says the request ended when a window is open but the link names nothing", async () => {
  vi.mocked(api.getOAuthRequest).mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
  vi.mocked(api.getOAuthSummary).mockResolvedValue({
    keys: { k1: { sign_ins: 0, window_open_until: new Date(NOW + 600_000).toISOString() } }, kill_switch: false });
  render(<OAuthRequestDialog link="abc" onClose={() => {}} />);
  await settle();
  await settle();
  expect(screen.getByText("This sign-in request has ended. Start again from the app.")).toBeInTheDocument();
});

it("says too many requests are waiting when the app's queue was full", async () => {
  vi.mocked(api.getOAuthRequest).mockRejectedValue(Object.assign(new Error("busy"), { status: 404, code: "oauth_busy" }));
  vi.mocked(api.getOAuthSummary).mockResolvedValue({
    keys: { k1: { sign_ins: 0, window_open_until: new Date(NOW + 600_000).toISOString() } }, kill_switch: false });
  render(<OAuthRequestDialog link="abc" onClose={() => {}} />);
  await settle();
  await settle();
  expect(screen.getByText("Too many sign-in requests are waiting. Wait a few minutes, then start again from the app.")).toBeInTheDocument();
  expect(screen.queryByText("This sign-in request has ended. Start again from the app.")).toBeNull();
});

it("explains a closed window without asking the server", async () => {
  render(<OAuthRequestDialog link="closed" onClose={() => {}} />);
  expect(screen.getByText(/accepts a new sign-in right now/)).toBeInTheDocument();
  expect(api.getOAuthRequest).not.toHaveBeenCalled();
});

it("reloads the signed-in apps when the key changes, so a rotation clears them", async () => {
  vi.mocked(api.getAccessKeyOAuth)
    .mockResolvedValueOnce(status({ sign_ins: [SIGN_IN] }))
    .mockResolvedValue(status());
  const { rerender } = render(<AccessKeyOAuth accessKeyId="k1" version="2026-11-10T11:00:00Z" />);
  await settle();
  expect(screen.getByText("Test App")).toBeInTheDocument();
  rerender(<AccessKeyOAuth accessKeyId="k1" version="2026-11-10T12:00:00Z" />);
  await settle();
  expect(screen.queryByText("Test App")).toBeNull();
  expect(screen.getByText("No app has signed in yet.")).toBeInTheDocument();
});

it("says sign-in can't work without an HTTPS address and keeps the switch off", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ oauth_enabled: false, public_url: null }));
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  expect(screen.getByRole("alert")).toHaveTextContent("no HTTPS address");
  expect(screen.getByRole("checkbox", { name: "Allow OAuth sign-in" })).toBeDisabled();
});

it("sends a panel on plain HTTP to the HTTPS address and offers nothing that would lead nowhere", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ window_open_until: new Date(NOW + 600_000).toISOString() }));
  const original = window.location;
  vi.spyOn(window, "location", "get").mockReturnValue({ ...original, protocol: "http:" } as Location);
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  expect(screen.getByRole("alert")).toHaveTextContent("OAuth is not available over HTTP. Please sign in at https://phx.example/ instead.");
  expect(screen.getByRole("link", { name: "https://phx.example/" })).toHaveAttribute("href", "https://phx.example/phoenix-mcp");
  expect(screen.getByRole("checkbox", { name: "Allow OAuth sign-in" })).toBeDisabled();
  expect(screen.queryByText("https://phx.example/api/phoenix-mcp")).toBeNull();
  expect(screen.queryByRole("button", { name: "Allow new sign-in" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop sign-in" })).toBeNull();
});

it("stops an open window at once", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ window_open_until: new Date(NOW + 600_000).toISOString() }));
  vi.mocked(api.closeOAuthWindow).mockResolvedValue(status());
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status());
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Stop sign-in" })); });
  await settle();
  expect(api.closeOAuthWindow).toHaveBeenCalledWith("k1");
  expect(screen.queryByText(/Sign-in open for/)).toBeNull();
  expect(screen.getByRole("button", { name: "Allow new sign-in" })).toBeInTheDocument();
});

it("shows a window opened elsewhere on the page in every copy of the key's sign-in", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status());
  vi.mocked(api.openOAuthWindow).mockResolvedValue(status({ window_open_until: new Date(NOW + 600_000).toISOString() }));
  render(<><div data-testid="a"><AccessKeyOAuth accessKeyId="k1" /></div><div data-testid="b"><AccessKeyOAuth accessKeyId="k1" compact /></div></>);
  await settle();
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ window_open_until: new Date(NOW + 600_000).toISOString() }));
  await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: "Allow new sign-in" })[0]); });
  await settle();
  expect(screen.getAllByText("Sign-in open for 10:00")).toHaveLength(2);
});

it("asks before removing a sign-in, and a click elsewhere cancels", async () => {
  vi.mocked(api.getAccessKeyOAuth).mockResolvedValue(status({ sign_ins: [SIGN_IN] }));
  vi.mocked(api.removeOAuthSignIn).mockResolvedValue(undefined);
  render(<AccessKeyOAuth accessKeyId="k1" />);
  await settle();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove the sign-in of Test App" })); });
  expect(api.removeOAuthSignIn).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  await act(async () => { fireEvent.pointerDown(document.body); });
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove the sign-in of Test App" })); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Confirm removing the sign-in of Test App" })); });
  await settle();
  expect(api.removeOAuthSignIn).toHaveBeenCalledWith("k1", "s1");
});

it("keeps the approval open on a click outside it or Escape", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue(REQUEST);
  const onClose = vi.fn();
  render(<OAuthRequestDialog link="r1" onClose={onClose} />);
  await settle();
  fireEvent.click(document.querySelector(".modal-backdrop") as Element);
  fireEvent.keyDown(document, { key: "Escape" });
  await settle();
  expect(api.denyOAuthRequest).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
});

it("counts down the key's sign-in window, not only the request", async () => {
  vi.mocked(api.getOAuthRequest).mockResolvedValue({
    ...REQUEST, keys: [{ ...REQUEST.keys[0], window_open_until: new Date(NOW + 120_000).toISOString() }],
  });
  render(<OAuthRequestDialog link="r1" onClose={() => {}} />);
  await settle();
  expect(screen.getByText("This request ends in 2:00")).toBeInTheDocument();
});
