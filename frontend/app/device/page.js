import { Suspense } from "react";
import DeviceApproval from "../components/DeviceApproval.js";

export const metadata = {
  title: "Approve PromptRail CLI",
  description: "Authorize a terminal installation of PromptRail.",
};

export default function DevicePage() {
  return (
    <Suspense fallback={<main className="device-approval-page" aria-label="Loading authorization" />}>
      <DeviceApproval />
    </Suspense>
  );
}
