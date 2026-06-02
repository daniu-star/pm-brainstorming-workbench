import InterviewClientPage from "./InterviewClientPage";

export function generateStaticParams() {
  return [{ id: "__placeholder__" }];
}

export const dynamicParams = true;

export default function InterviewPage() {
  return (
    <div className="fixed inset-0 z-50 bg-warm-50">
      <InterviewClientPage />
    </div>
  );
}
