import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ConnectInstructions } from "../components/ConnectInstructions";

describe("external client connection instructions", () => {
  it("opens Harness setup and keeps its configuration aligned with the editable URL", () => {
    render(<ConnectInstructions accessKey="phx_example" accessKeyName="Voice_Assist" />);
    const tab = screen.getByRole("tab", { name: "DeepSeek Harness (experimental)" });
    fireEvent.click(tab);
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Claude web/app" })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("desktop app or CLI");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("~/.dsh/profiles/desktop/cordis.patch.yml");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("otherwise append");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("CLI alternative");
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
});
