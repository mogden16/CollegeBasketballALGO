export const toLocalIsoDate = (value: Date): string => {
  const copy = new Date(value);
  copy.setMinutes(copy.getMinutes() - copy.getTimezoneOffset());
  return copy.toISOString().slice(0, 10);
};
