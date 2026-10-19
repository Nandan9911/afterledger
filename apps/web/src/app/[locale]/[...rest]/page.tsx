import { notFound } from 'next/navigation';

// any address no page answers: the page that says so, in the visitor's language
export default function CatchAll() {
  notFound();
}
