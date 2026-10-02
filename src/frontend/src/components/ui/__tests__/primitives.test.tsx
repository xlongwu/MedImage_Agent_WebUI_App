import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Dialog, Sheet } from "../overlays";
import { Badge, Button, EmptyState, IconButton, Progress, Skeleton, Tooltip } from "../primitives";
import { SegmentedControl } from "../segmented-control";
import { Table, TableEmpty } from "../table";

describe("ui primitives", () => {
  it("renders accessible buttons with icons and click handling", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();

    render(
      <Button
        leadingIcon={<span aria-hidden="true">+</span>}
        onClick={handleClick}
        variant="primary"
      >
        Review plan
      </Button>,
    );

    await user.click(screen.getByRole("button", { name: "Review plan" }));

    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("requires a visible accessible name for icon-only actions", () => {
    render(<IconButton label="Refresh runs">*</IconButton>);

    expect(screen.getByRole("button", { name: "Refresh runs" })).toBeInTheDocument();
  });

  it("renders compact state and empty-state primitives", () => {
    render(
      <EmptyState
        action={<Button size="sm">Import data</Button>}
        description="No DICOM batches are staged yet."
        title="Nothing queued"
      />,
    );

    expect(screen.getByText("Nothing queued")).toBeInTheDocument();
    expect(screen.getByText("No DICOM batches are staged yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import data" })).toBeInTheDocument();
  });

  it("renders badges, skeletons, and tooltip content", () => {
    render(
      <>
        <Badge tone="success">Ready</Badge>
        <Skeleton data-testid="loading-line" width={120} />
        <Tooltip label="Open context inspector">
          <button type="button">Inspect</button>
        </Tooltip>
      </>,
    );

    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByTestId("loading-line")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Open context inspector");
  });

  it("clamps progress and exposes an accessible value", () => {
    render(<Progress label="Overall progress" value={142} />);

    expect(screen.getByRole("progressbar", { name: "Overall progress" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
  });

  it("renders an indeterminate bar instead of a fake 0% fill when progress is unknown", () => {
    render(<Progress label="Overall progress" value={null} />);

    const bar = screen.getByRole("progressbar", { name: "Overall progress" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText("—")).toBeInTheDocument();
    // A determinate fill is the only thing that sets an inline width; none must be painted here.
    expect(bar.querySelector('[style*="width"]')).toBeNull();
  });

  it("renders controlled dialog and closes with Escape", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    render(
      <Dialog
        description="This action only affects the recent project listing."
        footer={<Button>Confirm</Button>}
        onOpenChange={onOpenChange}
        open={true}
        title="Remove project"
      />,
    );

    expect(screen.getByRole("dialog", { name: "Remove project" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("moves focus into overlays, traps Tab, and restores focus on close", async () => {
    const user = userEvent.setup();

    function DialogHarness() {
      const [open, setOpen] = useState(false);

      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open delete dialog
          </button>
          <Dialog
            footer={<Button>Confirm removal</Button>}
            onOpenChange={setOpen}
            open={open}
            title="Remove project"
          />
        </>
      );
    }

    render(<DialogHarness />);

    const trigger = screen.getByRole("button", { name: "Open delete dialog" });
    await user.click(trigger);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();
    });

    await user.tab();
    expect(screen.getByRole("button", { name: "Confirm removal" })).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "Close dialog" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Remove project" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("renders sheet content as a dismissible dialog surface", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    render(
      <Sheet onOpenChange={onOpenChange} open={true} title="Inspector">
        <p>Selected run context</p>
      </Sheet>,
    );

    expect(screen.getByRole("dialog", { name: "Inspector" })).toHaveTextContent(
      "Selected run context",
    );

    await user.click(screen.getByRole("button", { name: "Close sheet" }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("supports segmented keyboard navigation", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();

    render(
      <SegmentedControl
        aria-label="Plan view"
        onChange={handleChange}
        options={[
          { label: "Outline", value: "outline" },
          { label: "Graph", value: "graph" },
          { disabled: true, label: "JSON", value: "json" },
        ]}
        value="outline"
      />,
    );

    screen.getByRole("radio", { name: "Outline" }).focus();
    await user.keyboard("{ArrowRight}");

    expect(handleChange).toHaveBeenCalledWith("graph");
  });

  it("renders tables and empty rows with semantic markup", () => {
    render(
      <Table caption="DICOM series">
        <thead>
          <tr>
            <th>Subject</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <TableEmpty colSpan={2}>No series detected.</TableEmpty>
        </tbody>
      </Table>,
    );

    expect(screen.getByRole("table", { name: "DICOM series" })).toBeInTheDocument();
    expect(screen.getByText("No series detected.")).toHaveAttribute("colspan", "2");
  });
});
