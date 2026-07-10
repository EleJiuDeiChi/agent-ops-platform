import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Application } from "../main";

describe("Application", () => {
  it("renders the login surface before a session exists", () => {
    render(<Application />);

    expect(
      screen.getByRole("heading", { name: "AI 原生服务器运维面板" })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("账号")).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /直接进入/ })).toBeEnabled();
  });
});
