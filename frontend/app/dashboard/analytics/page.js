import ProductDashboard from "../../components/ProductDashboard";

export const metadata = {
  title: "PromptRail Analytics",
  description: "Usage analytics for PromptRail.",
};

export default function DashboardAnalyticsPage() {
  return <ProductDashboard activePage="analytics" />;
}
