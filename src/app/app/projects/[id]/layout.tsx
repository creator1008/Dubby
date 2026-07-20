export const dynamicParams = false;

export function generateStaticParams() {
  // Static shell; the real project id is supplied as ?id=<uuid> at runtime.
  return [{ id: "_" }];
}

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
