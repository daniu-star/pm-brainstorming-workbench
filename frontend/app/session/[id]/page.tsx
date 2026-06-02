import WorkbenchClientPage from "./WorkbenchClientPage";
import { ErrorBoundary } from "@/components/ErrorBoundary";

export default function WorkbenchPage() {
  return (
    <ErrorBoundary>
      <WorkbenchClientPage />
    </ErrorBoundary>
  );
}
