export function FeedbackRatingIcon({
  rating,
  size = 16,
  className = '',
}: {
  rating: 'positive' | 'negative';
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`${rating === 'negative' ? 'rotate-180' : ''} ${className}`}
    >
      <path d="M7 10v11H3V10h4zM7 19c3 1 5 2 9 2h1.2a2 2 0 001.9-1.4l1.8-6A2 2 0 0019 11h-5l1-5a2.5 2.5 0 00-4.5-2L7 10" />
    </svg>
  );
}
