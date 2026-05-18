export const dynamic = "force-dynamic";

export default function NotFound() {
  return (
    <div style={{ padding: "4rem 2rem", textAlign: "center" }}>
      <h1>404</h1>
      <p>The page you were looking for could not be found.</p>
      <p>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a href="/">Return home</a>
      </p>
    </div>
  );
}
