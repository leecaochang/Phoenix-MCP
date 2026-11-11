import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: { getAccessKeyOAuth: vi.fn(async () => ({
  oauth_enabled: true, window_open_until: null, kill_switch: false,
  public_url: "https://public.example/api/phoenix-mcp", max_sign_ins: 10, sign_ins: [],
})), patchAccessKey: vi.fn(), openOAuthWindow: vi.fn(), removeOAuthSignIn: vi.fn() } }));
import { ConnectInstructions } from "../components/ConnectInstructions";

describe("external client connection instructions", () => {
  it("opens Harness setup and keeps its configuration aligned with the editable URL", () => {
    render(<ConnectInstructions accessKey="phx_example" accessKeyName="Voice_Assist" />);
    const tab = screen.getByRole("tab", { name: "DeepSeek Harness (Preview)" });
    fireEvent.click(tab);
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Claude web/app" })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("desktop app or CLI");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("~/.dsh/profiles/desktop/cordis.patch.yml");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("otherwise append");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("CLI Alternative");
    expect(screen.getByRole("tabpanel")).toHaveTextContent('serverName: "voice-assist"');
    expect(screen.getByRole("tabpanel")).toHaveTextContent('Authorization: "Bearer phx_example"');
    fireEvent.change(screen.getByRole("textbox", { name: "MCP server URL" }), {
      target: { value: "https://ha.example/api/phoenix-mcp" },
    });
    expect(screen.getByRole("tabpanel")).toHaveTextContent('url: "https://ha.example/api/phoenix-mcp"');
    expect(screen.getByRole("link", { name: /Open.*DeepSeek Harness/ }))
      .toHaveAttribute("href", "https://www.deepseek.com/en/harness/");
    expect(screen.getByText(/mkdir -p ~\/\.dsh\/skills\/phoenix/))
      .toHaveTextContent("https://ha.example/api/phoenix-mcp/skill");
  });

  it("keeps the key and OAuth methods apart and opens on OAuth when the key allows it", async () => {
    render(<ConnectInstructions accessKey="phx_example" accessKeyName="phone" accessKeyId="k1" oauthEnabled />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "Sign In with OAuth" })).toHaveAttribute("aria-pressed", "true");
    // The OAuth side shows the switch and never the key.
    expect(screen.getByRole("checkbox", { name: "Allow OAuth sign-in" })).toBeChecked();
    expect(document.body.textContent).not.toContain("phx_example");
    expect(screen.getByRole("tab", { name: "ChatGPT" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Cursor" })).toBeNull();
    // Commands use the address the server reports for apps.
    expect(screen.queryByRole("tab", { name: "Codex" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Claude Code" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Claude" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("claude mcp add --transport http phoenix https://public.example/api/phoenix-mcp");
    // Switching method shows the key and its clients.
    fireEvent.click(screen.getByRole("button", { name: "Paste the Access Key" }));
    expect(document.body.textContent).toContain("phx_example");
    expect(screen.getByRole("tab", { name: "Cursor" })).toBeInTheDocument();
  });

  it("opens on the key method for a key without OAuth", () => {
    render(<ConnectInstructions accessKey="phx_example" accessKeyName="phone" />);
    expect(screen.getByRole("button", { name: "Paste the Access Key" })).toHaveAttribute("aria-pressed", "true");
  });
});
