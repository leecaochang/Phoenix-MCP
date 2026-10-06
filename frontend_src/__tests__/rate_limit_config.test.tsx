/** A save reply refreshes only the rate-limit fields the operator has not edited since. */
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));
import { api } from "../api";
import { RateLimitConfig } from "../components/RateLimitConfig";
import type { AccessKeyRecord } from "../types";

const KEY = { id: "k1", name: "alpha", rate_limit_requests: 10, rate_limit_burst: 3 } as AccessKeyRecord;

function Parent({ initial = KEY }: { initial?: AccessKeyRecord }) {
  const [accessKey, setAccessKey] = useState(initial);
  return (
    <>
      <RateLimitConfig accessKey={accessKey} onUpdate={setAccessKey} />
      <button onClick={() => setAccessKey({ ...accessKey, rate_limit_burst: 7 })}>external</button>
    </>
  );
}

beforeEach(() => vi.resetAllMocks());

const requests = () => screen.getByLabelText(/requests/i) as HTMLInputElement;
const burst = () => screen.getByLabelText(/burst/i) as HTMLInputElement;

it("keeps a burst edit typed while the requests save was in flight", async () => {
  let reply!: (key: AccessKeyRecord) => void;
  vi.mocked(api.patchAccessKey).mockReturnValueOnce(new Promise((r) => { reply = r; }));
  render(<Parent />);

  fireEvent.change(requests(), { target: { value: "20" } });
  fireEvent.blur(requests());
  expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { rate_limit_requests: 20, rate_limit_burst: 3 });
  fireEvent.change(burst(), { target: { value: "5" } });

  await act(async () => reply({ ...KEY, rate_limit_requests: 20, rate_limit_burst: 3 }));
  expect(requests().value).toBe("20");
  expect(burst().value).toBe("5");
});

it("keeps a newer edit that typed the old value back while a save was in flight", async () => {
  let reply!: (key: AccessKeyRecord) => void;
  vi.mocked(api.patchAccessKey).mockReturnValueOnce(new Promise((r) => { reply = r; }));
  render(<Parent />);

  fireEvent.change(requests(), { target: { value: "20" } });
  fireEvent.blur(requests());
  fireEvent.change(requests(), { target: { value: "10" } });

  await act(async () => reply({ ...KEY, rate_limit_requests: 20 }));
  expect(requests().value).toBe("10");
});

it("lets only the newest of several pending saves update the form", async () => {
  const replies: Array<(key: AccessKeyRecord) => void> = [];
  vi.mocked(api.patchAccessKey).mockImplementation(() => new Promise((r) => { replies.push(r); }));
  render(<Parent />);

  fireEvent.change(requests(), { target: { value: "20" } });
  fireEvent.blur(requests());
  fireEvent.change(requests(), { target: { value: "30" } });
  fireEvent.blur(requests());
  await act(async () => replies[0]({ ...KEY, rate_limit_requests: 20, rate_limit_burst: 3 }));
  expect(requests().value).toBe("30");

  fireEvent.change(burst(), { target: { value: "5" } });
  fireEvent.blur(burst());
  expect(vi.mocked(api.patchAccessKey).mock.calls[2][1]).toEqual({ rate_limit_requests: 30, rate_limit_burst: 5 });
  await act(async () => replies[1]({ ...KEY, rate_limit_requests: 30, rate_limit_burst: 3 }));
  await act(async () => replies[2]({ ...KEY, rate_limit_requests: 30, rate_limit_burst: 5 }));
  expect(requests().value).toBe("30");
  expect(burst().value).toBe("5");
});

it("still shows a saved value the operator had not edited", () => {
  render(<Parent />);
  fireEvent.click(screen.getByRole("button", { name: "external" }));
  expect(burst().value).toBe("7");
  expect(requests().value).toBe("10");
});
