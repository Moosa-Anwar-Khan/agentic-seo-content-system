import type { Metadata } from 'next';
import PortalShell from '../components/PortalShell';
import './globals.css';
export const metadata: Metadata = { title: 'Content workspace | Muster & Partner', robots: { index: false, follow: false } };
export default function Layout({children}:{children:React.ReactNode}) {
 return <html lang="en"><body><PortalShell>{children}</PortalShell></body></html>;
}
