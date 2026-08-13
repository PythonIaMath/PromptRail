import LossDashboard from "../dashboard/LossDashboard";

export const metadata = {
  title: "PromptRail Loss Dashboard",
  description: "Local Modal training loss monitor for PromptRail.",
};

export default function TrainingDashboardPage() {
  return <LossDashboard />;
}
