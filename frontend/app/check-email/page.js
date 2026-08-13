import CheckEmail from "../components/CheckEmail";

export const metadata = {
  title: "Check your email | PromptRail",
  description: "Open the sign-in link sent to your email address.",
};

export default async function CheckEmailPage({ searchParams }) {
  const { email } = await searchParams;

  return <CheckEmail email={typeof email === "string" ? email : ""} />;
}
