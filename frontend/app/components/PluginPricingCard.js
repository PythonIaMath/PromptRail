export const pluginSubscriptionPlans = [
  {
    interval: "month",
    label: "Monthly",
    price: "$10",
    cadence: "/month",
    detail: "Cancel anytime",
  },
  {
    interval: "year",
    label: "Annual",
    price: "$100",
    cadence: "/year",
    detail: "Save $20 each year",
  },
];

export default function PluginPricingCard({ children, option, selected = false }) {
  return (
    <section className={`plugin-onboarding-price${selected ? " is-selected" : ""}`}>
      <span>{option.label}</span>
      <strong>
        {option.price}<small>{option.cadence}</small>
      </strong>
      <p>{option.detail}</p>
      <div className="plugin-onboarding-refund">
        <span>Finish your Codex/Claude subscription with our plugin active?</span>
        <strong>We <b>refund</b> you.</strong>
      </div>
      {children}
    </section>
  );
}
