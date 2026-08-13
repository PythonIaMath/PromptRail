import ProductDashboard from "../../components/ProductDashboard";

export const metadata = {
  title: "PromptRail Credit",
  description: "Credit balance and billing for PromptRail.",
};

export default function DashboardCreditPage() {
  return <ProductDashboard activePage="credit" />;
}
