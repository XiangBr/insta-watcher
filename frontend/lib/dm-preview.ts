export const SAMPLE_USERNAME = "maria";

/** `{username}` is plain substitution, the same rule the worker applies when it sends. */
export function renderDm(template: string, username: string = SAMPLE_USERNAME): string {
  return template.replaceAll("{username}", username);
}

export type DmPreview = {
  text: string;
  button: { title: string; url: string } | null;
};

/**
 * What the person will receive. Mirrors the worker: with a button label the link rides in
 * the button; without one it is appended to the text unless the text already has it.
 */
export function previewDm(input: {
  message: string;
  linkUrl: string;
  buttonLabel: string;
}): DmPreview {
  const text = renderDm(input.message);
  const link = input.linkUrl.trim();
  const label = input.buttonLabel.trim();
  if (link && label) return { text, button: { title: label, url: link } };
  if (link && !text.includes(link)) return { text: text ? `${text}\n${link}` : link, button: null };
  return { text, button: null };
}
