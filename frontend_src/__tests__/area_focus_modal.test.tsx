/**
 * A dialog owns focus while it is open. When the focused control inside it goes
 * away, focus stays in the dialog, never on the toolbar behind the backdrop.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState, type ReactNode } from "react";
import { useAreaKeys } from "../shell/areaFocus";
import { Modal } from "../components/Modal";
import { TagInput } from "../components/TagInput";
import { InlineConfirm } from "../ui/InlineConfirm";
import { MenuRootContext } from "../ui/Menu";

const settle = (ms = 20) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
const inDialog = () => (document.activeElement as HTMLElement).closest(".modal");

beforeEach(() => {
  // jsdom lays nothing out, and an element with no box does not count as shown.
  Element.prototype.getBoundingClientRect = function box() {
    return { top: 0, left: 0, width: 100, height: 50, right: 100, bottom: 50, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});

function Panel({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement | null>(null);
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  useAreaKeys(root);
  return (
    <MenuRootContext.Provider value={el}>
      <div ref={(node) => { root.current = node; if (node && !el) setEl(node); }}>
        <div className="phx-toolbar"><button>Toolbar</button></div>
        {children}
      </div>
    </MenuRootContext.Provider>
  );
}

function ChipEditor() {
  const [tags, setTags] = useState(["ns.a", "ns.b"]);
  return (
    <Modal titleId="t" overWorkspace onClose={() => {}}>
      <h3 id="t">Title</h3>
      <TagInput value={tags} onChange={setTags} canonicalTags={["ns.a", "ns.b", "ns.c"]} />
      <button>Save</button>
    </Modal>
  );
}

function DeleteEditor({ keepOpener }: { keepOpener: boolean }) {
  const [asking, setAsking] = useState(false);
  return (
    <Modal titleId="t" overWorkspace onClose={() => {}}>
      <h3 id="t">Title</h3>
      {asking && <InlineConfirm label="Delete" confirmLabel="Yes" cancelLabel="Keep" onConfirm={() => {}} onCancel={() => setAsking(false)} />}
      {(keepOpener || !asking) && <button onClick={() => setAsking(true)}>Delete</button>}
      <button>Save</button>
    </Modal>
  );
}

describe("focus lost inside a dialog with no panel around it", () => {
  const removeButton = (root: ParentNode) => [...root.querySelectorAll("button")].find((button) => button.className === "tag-chip-x")!;
  const press = (control: HTMLElement) => {
    control.focus();
    fireEvent.click(control);
  };
  function BareChips() {
    const [tags, setTags] = useState(["ns.a", "ns.b"]);
    return (
      <Modal titleId="t" onClose={() => {}}>
        <h3 id="t">Title</h3>
        <TagInput value={tags} onChange={setTags} canonicalTags={["ns.a", "ns.b", "ns.c"]} />
        <button>Save</button>
      </Modal>
    );
  }

  it("stays in the dialog when a tag's remove button goes away", async () => {
    render(<div><button>Behind</button><BareChips /></div>);
    await settle(0);
    press(removeButton(document));
    await settle();
    expect(inDialog()).toBeTruthy();
    await userEvent.tab();
    expect(inDialog()).toBeTruthy();
  });

  it("stays in the dialog when the control it opened on goes away", async () => {
    render(<BareChips />);
    await settle(0);
    const remove = removeButton(document);
    expect(document.activeElement).toBe(remove);
    fireEvent.click(remove);
    await settle();
    expect(inDialog()).toBeTruthy();
  });

  it("stays in the dialog inside a shadow root, as the injector mounts it", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    const mount = document.createElement("div");
    shadow.appendChild(mount);
    render(<BareChips />, { container: mount });
    await settle(0);
    press(removeButton(shadow));
    await settle();
    expect(document.activeElement).toBe(host);
    expect((shadow.activeElement as HTMLElement).closest(".modal")).toBeTruthy();
    host.remove();
  });

  it("gives way to the top dialog of two", async () => {
    function Nested() {
      const [open, setOpen] = useState(false);
      const [gone, setGone] = useState(false);
      return (
        <Modal titleId="o" onClose={() => {}}>
          <h3 id="o">Outer</h3>
          <button onClick={() => setOpen(true)}>Outer control</button>
          {open && (
            <Modal titleId="i" onClose={() => {}}>
              <h3 id="i">Inner</h3>
              <button>Inner control</button>
              {!gone && <button onClick={() => setGone(true)}>Remove me</button>}
            </Modal>
          )}
        </Modal>
      );
    }
    render(<Nested />);
    await settle(0);
    fireEvent.click(screen.getByText("Outer control"));
    await settle(0);
    press(screen.getByText("Remove me"));
    await settle();
    expect(document.activeElement).toBe(screen.getByText("Inner control"));
  });

  it("leaves focus alone when a control that did not hold it goes away", async () => {
    function Other() {
      const [gone, setGone] = useState(false);
      return (
        <Modal titleId="t" onClose={() => {}}>
          <h3 id="t">Title</h3>
          <button>First</button>
          <button onClick={() => setGone(true)}>Second</button>
          {!gone && <button>Removed</button>}
        </Modal>
      );
    }
    render(<Other />);
    await settle(0);
    const second = screen.getByText("Second");
    press(second);
    await settle();
    expect(document.activeElement).toBe(second);
  });

  it("leaves focus that its control sent out of the dialog before going away", async () => {
    function Mover() {
      const [gone, setGone] = useState(false);
      const outside = useRef<HTMLButtonElement>(null);
      return (
        <>
          <button ref={outside}>Outside</button>
          <Modal titleId="t" onClose={() => {}}>
            <h3 id="t">Title</h3>
            <button>First</button>
            {!gone && <button onClick={() => { outside.current?.focus(); setGone(true); }}>Remove me</button>}
          </Modal>
        </>
      );
    }
    render(<Mover />);
    await settle(0);
    press(screen.getByText("Remove me"));
    await settle();
    expect(document.activeElement).toBe(screen.getByText("Outside"));
  });

  it("leaves focus the reader took out of the dialog while its control stays", async () => {
    function Changing() {
      const [more, setMore] = useState(false);
      return (
        <Modal titleId="t" onClose={() => {}}>
          <h3 id="t">Title</h3>
          <button onClick={() => setMore(true)}>First</button>
          {more && <p>More</p>}
        </Modal>
      );
    }
    render(<Changing />);
    await settle(0);
    const first = screen.getByText("First");
    first.blur();
    fireEvent.click(first);
    await settle();
    expect(screen.getByText("More")).toBeTruthy();
    expect(document.activeElement).toBe(document.body);
  });

  it("gives focus back to the opener when it closes, not to itself", async () => {
    function Closing() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Opener</button>
          {open && <Modal titleId="t" onClose={() => {}}><h3 id="t">Title</h3><button onClick={() => setOpen(false)}>Close</button></Modal>}
        </>
      );
    }
    render(<Closing />);
    await settle(0);
    press(screen.getByText("Opener"));
    await settle(0);
    press(screen.getByText("Close"));
    await settle();
    expect(document.querySelector(".modal")).toBeNull();
    expect(document.activeElement).toBe(screen.getByText("Opener"));
  });
});

describe("focus lost inside an open dialog", () => {
  it("stays in the dialog when a tag's remove button goes away", async () => {
    render(<Panel><ChipEditor /></Panel>);
    await settle(0);
    const remove = screen.getAllByRole("button").find((button) => button.className === "tag-chip-x")!;
    remove.focus();
    await userEvent.click(remove);
    await settle();
    expect(document.querySelector(".modal")).toBeTruthy();
    expect(inDialog()).toBeTruthy();
  });

  it("lands on the top dialog of two", async () => {
    function Nested() {
      const [open, setOpen] = useState(false);
      const [gone, setGone] = useState(false);
      return (
        <Modal titleId="o" overWorkspace onClose={() => {}}>
          <h3 id="o">Outer</h3>
          <button onClick={() => setOpen(true)}>Outer control</button>
          {open && (
            <Modal titleId="i" overWorkspace onClose={() => {}}>
              <h3 id="i">Inner</h3>
              <button>Inner control</button>
              {!gone && <button onClick={() => setGone(true)}>Remove me</button>}
            </Modal>
          )}
        </Modal>
      );
    }
    render(<Panel><Nested /></Panel>);
    await settle(0);
    await userEvent.click(screen.getByText("Outer control"));
    await settle(0);
    await userEvent.click(screen.getByText("Remove me"));
    await settle();
    expect(document.activeElement).toBe(screen.getByText("Inner control"));
  });

  it("leaves focus the user moved elsewhere in the dialog alone", async () => {
    render(<Panel><ChipEditor /></Panel>);
    await settle(0);
    screen.getByText("Save").focus();
    await settle();
    expect(document.activeElement).toBe(screen.getByText("Save"));
  });

  it("still goes to the toolbar when no dialog is open", async () => {
    function Plain() {
      const [gone, setGone] = useState(false);
      return !gone ? <button onClick={() => setGone(true)}>Remove me</button> : null;
    }
    render(<Panel><Plain /></Panel>);
    await settle(0);
    await userEvent.click(screen.getByText("Remove me"));
    await settle();
    expect((document.activeElement as HTMLElement).className).toBe("phx-toolbar");
  });

  it("ignores a dialog in a workspace not on show", async () => {
    function Hidden() {
      const [gone, setGone] = useState(false);
      return (
        <>
          <div hidden><Modal titleId="t" onClose={() => {}}><h3 id="t">Title</h3><button>Inside</button></Modal></div>
          {!gone && <button onClick={() => setGone(true)}>Remove me</button>}
        </>
      );
    }
    render(<Panel><Hidden /></Panel>);
    await settle(0);
    const button = screen.getByText("Remove me");
    button.focus();
    await userEvent.click(button);
    await settle();
    expect((document.activeElement as HTMLElement).className).toBe("phx-toolbar");
  });
});

describe("a confirmation closing inside a dialog", () => {
  it("stays in the dialog when the control that opened it was removed", async () => {
    render(<Panel><DeleteEditor keepOpener={false} /></Panel>);
    await settle(0);
    await userEvent.click(screen.getByText("Delete"));
    await settle(0);
    await userEvent.click(screen.getByText("Keep"));
    await settle();
    expect(inDialog()).toBeTruthy();
  });

  it("stays in the dialog without the panel's focus rescue around it", async () => {
    render(<DeleteEditor keepOpener={false} />);
    await settle(0);
    await userEvent.click(screen.getByText("Delete"));
    await settle(0);
    await userEvent.click(screen.getByText("Keep"));
    await settle();
    expect(inDialog()).toBeTruthy();
  });

  it("returns to the control that opened it when that control stayed", async () => {
    render(<Panel><DeleteEditor keepOpener /></Panel>);
    await settle(0);
    await userEvent.click(screen.getByText("Delete"));
    await settle(0);
    await userEvent.click(screen.getByText("Keep"));
    await settle();
    expect(document.activeElement).toBe(screen.getByText("Delete"));
  });
});

describe("dialogs and the area keys", () => {
  it("returns to the opener in the outer dialog, and F6 and Tab stay in the inner one", async () => {
    function Nested() {
      const [open, setOpen] = useState(false);
      return (
        <Modal titleId="o" overWorkspace onClose={() => {}}>
          <h3 id="o">Outer</h3>
          <button onClick={() => setOpen(true)}>Open inner</button>
          <button>Other</button>
          {open && (
            <Modal titleId="i" overWorkspace onClose={() => setOpen(false)}>
              <h3 id="i">Inner</h3>
              <button onClick={() => setOpen(false)}>Close inner</button>
            </Modal>
          )}
        </Modal>
      );
    }
    render(<Panel><Nested /></Panel>);
    await settle(0);
    await userEvent.click(screen.getByText("Open inner"));
    await settle(0);
    expect(document.activeElement?.textContent).toBe("Close inner");
    fireEvent.keyDown(document.activeElement!, { key: "F6", bubbles: true });
    expect(document.activeElement?.textContent).toBe("Close inner");
    await userEvent.tab();
    expect(document.activeElement?.textContent).toBe("Close inner");
    await userEvent.click(document.activeElement as HTMLElement);
    await settle();
    expect(document.activeElement?.textContent).toBe("Open inner");
  });
});
