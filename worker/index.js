// The Worker that serves every Publick site from the sites bucket.
// Bindings (wrangler.toml): SITES, the R2 bucket. The logic is in sites.js:
// this module may export only the handler, since Workers treat any other
// export as an entry point.
import { handle } from "./sites.js";

export default { fetch: handle };
