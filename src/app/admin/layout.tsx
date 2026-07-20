import { AppShell } from "@/components/app/AppShell";
import { AuthBoundary } from "@/components/app/AuthBoundary";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthBoundary>
      <AppShell>{children}</AppShell>
    </AuthBoundary>
  );
}
