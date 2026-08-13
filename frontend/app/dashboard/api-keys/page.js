import ProductDashboard from "../../components/ProductDashboard";

export const metadata = {
  title: "PromptRail API Keys",
  description: "API key management for PromptRail.",
};

export default function DashboardApiKeysPage() {
  return <ProductDashboard activePage="apiKeys" />;
}
