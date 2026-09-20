# How to patch telegram-tt with the NearByPanel

## Step 1 — Clone telegram-tt

```bash
git clone https://github.com/Ajaxy/telegram-tt
cd telegram-tt
cp .env.example .env
# Fill in your api_id and api_hash from https://my.telegram.org
```

## Step 2 — Copy our component

```bash
cp ../NearByClub/telegram-tt-patch/NearByPanel.tsx src/components/NearByPanel.tsx
```

## Step 3 — Inject into App.tsx

Open `src/App.tsx` and add near the bottom of the returned JSX, just before the closing `</div>`:

```tsx
// Add import at top:
import NearByPanel from './components/NearByPanel';

// Add inside the main App JSX (after all other panels):
{currentUserId && (
  <NearByPanel tgUserId={currentUserId} />
)}
```

> `currentUserId` is already available in telegram-tt's global state.
> Use the existing `selectCurrentUserId` selector from `src/global/selectors`.

## Step 4 — Add env variable

In `.env`:
```
NEARBY_SERVER_URL=http://localhost:8000
```

In `webpack.config.js` or `vite.config.ts`, expose it:
```js
'process.env.NEARBY_SERVER_URL': JSON.stringify(process.env.NEARBY_SERVER_URL)
```

## Step 5 — Run

```bash
npm install
npm run dev
```

The NearByPanel will appear as a floating widget in the bottom-right corner,
showing nearby count, crossing alerts, and the radius slider.

---

## How the A/B/C experiment works

The server assigns each user to a group (stored in DB):
- **Group A** — sees minimal crossing alert (emoji + ETA only)
- **Group B** — sees context hint ("someone you may know")  
- **Group C** — no alert (control group, used as baseline)

The `hint` field in the crossing payload controls which copy is shown.
GPS vector change after notification is measured server-side automatically.
