type SkillDisplayNameProps = {
  name: string;
  slug: string;
};

export default function SkillDisplayName({ name, slug }: SkillDisplayNameProps) {
  return (
    <>
      {name}
      {name !== slug && (
        <span className="font-mono font-normal text-text-tertiary dark:text-text-tertiary-dark">
          （{slug}）
        </span>
      )}
    </>
  );
}
