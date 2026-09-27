# SkiDash

Snow forecast and road status for Nakiska, Norquay, Lake Louise, Sunshine, Fernie,
Panorama and WinSport, with roads measured from the Hwy 1 / Hwy 22 interchange.

- Weather: Open-Meteo, fetched live in the browser.
- Roads: 511 Alberta (events + road conditions) and DriveBC, fetched every ~15 min
  by a GitHub Action and published as `roads.json`. The Alberta key never reaches the browser.

## Repo layout

    index.html
    README.md
    scripts/fetch_roads.py
    .github/workflows/deploy.yml

## Setup

1. Upload all four files, keeping the folder structure. `.github` starts with a dot;
   if your file browser hides it, create the file in GitHub's web editor at
   `.github/workflows/deploy.yml` and paste the contents.
2. Settings > Pages > Build and deployment > Source: **GitHub Actions**.
3. Settings > Secrets and variables > Actions > New repository secret.
   Name `AB511_KEY`, value your 511 Alberta developer key.
4. Actions tab > "Build and deploy SkiDash" > Run workflow. After it goes green the
   site is at `https://<username>.github.io/<repo>/`.

## Behaviour

- Each hill shows a road chip: Roads clear, Caution, Closure, or Roads not checked
  (a province's feed failed or the Alberta key is missing). A known closure always shows.
- Fernie shows both routes (Hwy 22, and Hwy 2 via Fort Macleod) and the chip points you
  to the open one if the other is closed.
- Planned future work is listed but does not affect the chip.
- Hill access roads (Sunshine Rd, Norquay Rd, Toby Creek Rd, Fernie Ski Hill Rd) are not
  reported by either province.

## Notes

- GitHub can delay scheduled runs, so "Roads checked" is typically 15 to 30 minutes.
  The page warns when road data is over an hour old.
- GitHub disables scheduled workflows in public repos after 60 days with no repo activity.
  It emails you first; re-enable from the Actions tab. Expect this over summer.
- Run the fetcher locally: `AB511_KEY=yourkey python3 scripts/fetch_roads.py roads.json`

## Roadmap

- Phase 3: best route and drive time from the Petro-Canada at Hwy 1 and Hwy 22
- Phase 4: optional live traffic
