// Copie dans static/fonts/ les seules polices nécessaires (woff2, sous-ensemble latin)
// et leurs licences. Lancer après « npm install » : npm run fonts
import { copyFileSync, mkdirSync } from "node:fs";

const FONTS = {
  poppins: [400, 500, 600, 700],
  roboto: [400, 500, 700],
};
const out = "static/fonts";
mkdirSync(out, { recursive: true });
for (const [family, weights] of Object.entries(FONTS)) {
  const src = `node_modules/@fontsource/${family}`;
  for (const w of weights) {
    const name = `${family}-latin-${w}-normal.woff2`;
    copyFileSync(`${src}/files/${name}`, `${out}/${name}`);
  }
  copyFileSync(`${src}/LICENSE`, `${out}/LICENSE-${family}.txt`);
}
console.log("Polices copiées dans", out);
