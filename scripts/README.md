# コンテンツ生成と GitHub Pages

## 投稿と出力

- `data/sources/blog/` の `.md`、`data/sources/training/` の Garmin `.fit`、`data/sources/sample/` の `.csv` を再帰的に読み取り、それぞれ `data/contents/{種別}/` に独立した記事 HTML を生成します。拡張子の大文字・小文字は区別しません。サブディレクトリの構造は維持します。
- ブログとサンプルではファイル名の日付は必須ではありません。先頭が実在する `YYYYMMDD` または `YYYY-MM-DD` の場合、その日付を使用し、出力名の日付を `YYYY-MM-DD` に正規化します。後ろに文字列を付けることもできます（例: `20250105-morning.md` → `2025-01-05-morning.html`）。日付がない場合や日付として無効な場合は、元のベース名をそのまま HTML のファイル名にし、日付欄を「日付不明」とします。更新日時など環境依存の値は使いません。
- ブログのタイトルは先頭の `<!-- title: タイトル -->`、次に最初の H1 見出し、それもない場合は日付（例: `2025-01-05 のブログ`）、日付不明なら拡張子を除いた元のファイル名を使います。本文は `cmarkgfm` の GFM（表、タスクリスト、自動リンクなど）と脚注を静的 HTML に変換し、`nh3` でサニタイズします。見出しには GitHub 風の ID を生成し、同じ見出しは `-1`、`-2` などで区別します。脚注は `[^注]` と `[^注]: 注釈` で記述でき、参照元と脚注の間を往復できます。GitHub のサイト固有機能や、許可されていない生 HTML が完全に同一に再現されるわけではありません。
- トレーニングの FIT は Garmin 公式 `garmin-fit-sdk` でデコードします。元と同じベース名の `.json` と、JSON 内 `file_id_mesgs.time_created` を日本標準時に変換した `YYYY-MM-DD_HH-MM-SS.html` を `data/contents/training/` に生成します。HTML は元 FIT と生成 JSON の GitHub 上のファイルへのリンク、セッションの概要とアクティブ・ラップの表を表示します。壊れた FIT、日時の欠落、同じ出力先への衝突はエラーです。従来の training CSV は変換せず、処理時にスキップした旨をログに表示します。元ファイルは削除しません。
- Training 記事のタイトルは FIT に記録された種別とファイル作成日時から生成し、`running` は `Jog`、`swimming` は `Swim` と表示します。それ以外は FIT の種別名、記録がない場合は `Activity` とし、複数種別があれば `Jog / Swim` のように併記します。左ペインの Training カテゴリーはファイル作成日時の日本標準時を使い「年 → 月 → 記事」に分類し、開いている記事の階層を展開します。
- サンプルの CSV は UTF-8（BOM 可）、先頭行を見出し、2行目以降をデータとして扱います。すべての行の列数は見出しと一致させてください。セルは HTML エスケープされます。
- 種別ディレクトリの `index.html` はテンプレートで、`<!-- ARTICLE_CONTENT -->` を1か所、`__ARTICLE_TITLE__` と `__ARTICLE_DATE__` をそれぞれ1か所以上含めてください。ルートへの参照は `../../../`、同種別の CSS/JS は `blog.css` / `blog.js` 等と記述します。ネストした記事ではこれらの参照を深さに合わせて修正します。テンプレート自体は `data/contents/{種別}/index.html` として公開しません。
- 各種別のサムネイルは `data/sources/blog/`、`data/sources/sample/`、`data/sources/training/` の直下に `thumbnail.jpg`、`thumbnail.png` または `thumbnail.gif` を配置します。複数ある場合は JPG、PNG、GIF の順で優先します。画像がない種別では `assets/junichiwatanuki.jpg` を使います。追加・変更・削除は次のビルドで反映されます。
- 種別内の媒体は同じ相対位置で出力され、`data/sources/` 内の種別外共有メディアも `data/contents/` に保持します。サイト内の明示的な `data/sources/...` 参照用にもメディアのコピーを残します。サイトルートや `data/` 内に置かれたソース外の静的メディアも公開用ディレクトリへコピーします。相対パスのソース記事リンクは対応する HTML へ変換され、クエリ・フラグメントは維持されます。実ファイル名の空白、日本語、`#`、`?`、`%` などは公開 URL と `index.json` でパーセントエンコードします。Markdown のリンク先に含まれるファイル名の `#`、`?`、`%` は区切り文字と区別するため、それぞれ `%23`、`%3F`、`%25` と記述してください。リポジトリ外・別サーバーのリソースを自動取得することはありません。
- カテゴリと記事のメニュー、記事メタデータを公開用 `index.json` に生成します。日付ありは日付降順、同日は URL 降順、日付不明はその後ろに URL 降順で並びます。ローカルプレビューでは作業ツリーのルート `index.json` は生成・更新しません。GitHub Actions では生成後、公開用 `.site/index.json` と同じ内容をルートの `index.json` に保存します。ルートの `index.json` は編集元ではなく生成物のため、手編集は次回の生成時に上書きされます。
- ブラウザーはページの読み込み時に `index.json` のキャッシュを再検証します。すでに開いているページや「戻る」操作で復元されたページは自動更新されないため、必要に応じて再読み込みしてください。記事のソース名を変更すると記事の URL も変わりますが、旧 URL からの転送は自動作成しません。
- 同じ出力先になる記事や既存メディアとの衝突、見出し・脚注・テンプレート間の ID 衝突、ソースツリーのシンボリックリンク、パス内のバックスラッシュ・制御文字は、曖昧または危険な入力としてエラーにします。

## ブログのカスタムコードブロック

`data/sources/blog/` 内のMarkdownで、次のようにコードフェンスの言語名と本文にサービスのURLを記述すると、記事生成時に埋め込み表示に変換します。

````markdown
```youtube
https://www.youtube.com/watch?v=B74K2b7IZDw
```
```instagram
https://www.instagram.com/p/DS4OOh9CXo0/
```
```twitter
https://x.com/j_watanuki/status/2107102704682344583
```
```garmin
https://connect.garmin.com/app/activity/25084785219
```
```slideshare
https://www.slideshare.net/slideshow/ss-41306350/41306350
```
````

URLは1ブロックにつき1件のHTTPS URLで指定します。元の投稿を開くリンクを併記し、不正または対応していない形式は従来どおりコードとして表示します。Xの埋め込みには公式スクリプトとJavaScriptが必要です。表示には各サービスの公開設定と埋め込み許可が必要で、削除や非公開、認証、広告ブロッカーなどにより表示されない場合があります。Instagramのカルーセルは `img_index` で指定した画像から始まることを保証できません。SlideShareは数値IDの埋め込みURLからサービス側の埋め込みキーへの転送に依存します。転送に対応しない場合は、SlideShareの共有画面で表示される `https://www.slideshare.net/slideshow/embed_code/key/…` を使用してください。外部サービスを表示すると閲覧者のブラウザからそれらのサービスに通信が発生します。

`rawhtml` フェンス内は、スクリプトを含めてHTMLとしてそのままページに挿入します。この機能はリポジトリのMarkdownを編集できる信頼済みの投稿者向けであり、任意のスクリプトがサイトと同じ権限で動作します。信頼できないHTMLを貼り付けないでください。Issueコメントは従来どおりサニタイズされ、`rawhtml` フェンスや埋め込みURLを実行しません。

### e2d3によるデータの可視化

ブログ記事の `e2d3` フェンスでは、`# template` の次の行に [e2d3-contrib](https://github.com/e2d3/e2d3-contrib) のディレクトリ名を記述し、`# data` の次の行からCSVを記述します。`force-directed-graph` ではCSVまたはタブ区切りのTSVを使用できます。セクション間に空行を入れることもできます。

````markdown
```e2d3
# template
bipartite-graph-original

# data
男子,女子,,
年代階級,完泳時間,男子,女子
高校生,02:00～02:14,2,1
高校生,02:15～02:29,3,1
```
````

`bipartite-graph-original` のCSVは1行目に2つの図のタイトル、2行目に4列の見出し、3行目以降に「左カテゴリ、右カテゴリ、1つ目の数値、2つ目の数値」を指定します。各カテゴリの組み合わせは重複させず、数値は有限の0以上の値にしてください。左カテゴリは23種類までです。公式テンプレートには先頭の明細行を読み飛ばす問題があるため、生成時に読み飛ばし用の行を追加して、元の明細をすべて反映します。

`InfiniteElementMethod_E2D3-LIG_03` は見出しなしの4列の明細で、最初の行から描画対象です。左・右カテゴリの組は重複させず、3列目には有限の0以上の数値を指定します。4列目は公式テンプレートでは描画に使われません。左右それぞれ37カテゴリまでです。固定版の公式既定CSVには末尾の閉じ引用符の欠落があるため、その公式ファイルだけを検証したうえで補正して使用します。記事のCSVは自動補正しません。

`force-directed-graph` は見出しを `id,label,source,target,value,color,size,width,distance` の順で指定します。TSVの場合は各項目を実際のタブで区切ってください。ノードの `id` は出現順の0から始まる連番で、`source`・`target` はその番号を参照します。各行に辺の情報を指定し、辺のみの行は `id`・`label` を空にします。ノードのサイズ、色、辺の幅・距離を指定できます。入力TSVは列の空欄や引用符を保って読み取り、ランタイム用のCSVに変換します。

`virtual-frostbite-halfmarathon` は `athlete,HH,MM,SS,acolor` の順のCSVを使用します。時間は0より大きく3時間以内、分・秒は0〜59、色列は `ffffff` のような6桁の16進数です。公式実装と同様、描画色は色列ではなくカテゴリ別の色が使われます。テンプレートの反復周期は3時間の記録を50倍速にした216秒です。背景画像とコース・走者を同じ座標上に重ねます。

入力が空、CSV・TSVの構文や検証可能な形式に不適合の場合、公式テンプレートの既定データを使用し、図の近くにその旨を表示します。上記の3種類のほか、`bipartite-graph-original`、`multi-series-line-chart`、`piechart-javascript` はテンプレート固有の形式も検証します。それ以外は主に既定データの列数と数値列から推定した簡易判定です。テンプレートごとにデータの意味や必要なライブラリが異なり、公式には統一された検証スキーマがないため、**すべての不適合を判定して既定データに切り替えることはできません**。入力が受理されても図が正しく表示されない場合があるため、テンプレートの説明と表示結果を確認してください。

GitHub Actionsとローカルビルドは、公式e2d3 0.6.4のブラウザ用ランタイムと固定コミット `d48bd8256e95cfcc393d37e3e7769000dd738fc9` のe2d3-contribをビルド時に取得し、図ごとのHTMLと必要なファイルを `data/contents/blog/_e2d3/` に生成します。`main.js` を持つ公式テンプレートを対象としますが、追加の依存設定、既定データの形式、外部データなどの理由で利用できないものもあります。使用できない場合はビルドを失敗させ、理由をログに表示します。1サイトにつき異なるグラフは100件、1件の入力は2 MiBまでです。

稼働中のNodeサーバーは不要ですが、初回を含めe2d3のビルド時にはインターネット接続が必要です。各テンプレートのJavaScriptはiframe内で実行されますが、サイトと同じオリジンの権限を持ちます。信頼済みの公式テンプレートのみ使用し、Issueコメントではe2d3フェンスを実行しません。テンプレート固有の外部ライブラリを利用する場合、閲覧者側でもネットワーク接続が必要なものがあります。生成物には公式コードとその依存ライブラリのライセンスを含めます。

### Mermaid・STL・SVGのプレビュー

ブログ記事では `rawmermaid`、`rawstl`、`rawsvg` のフェンスを使用すると、その中身をプレビューします。`mermaid`、`stl`、`svg` は図形として描画せず、通常のシンタックスハイライト付きコードとして表示します。

````markdown
```rawmermaid
flowchart LR
  A[開始] --> B[完了]
```

```rawstl
solid triangle
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
  endfacet
endsolid triangle
```

```rawsvg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="40" fill="steelblue" />
</svg>
```
````

STLは上記のようなASCIIテキストのほか、1行のSTLファイルの相対パスまたはHTTP(S) URLも指定できます。別オリジンのファイルは配信側がCORSを許可する必要があり、URLからはバイナリSTLも読み込めます。サイズ上限は25 MiBです。3Dモデルはドラッグで回転、右ドラッグで移動、ホイールやピンチで拡大・縮小できます。SVGは画像として表示し、SVG内のスクリプトは実行しません。インラインSVGと異なり、外部リソースやページのCSSは使えない場合があります。

MermaidとSTLの3D表示、シンタックスハイライトは、閲覧者のブラウザで固定バージョンの外部ライブラリを読み込みます。通信できない場合は元のコードを表示し、STLはWebGL対応ブラウザが必要です。表示できない構文や無効なファイルの場合も元のコードを確認できます。これらのプレビューは信頼済みのブログ記事だけを対象とし、Issueコメントのコードは描画しません。

## フッターのSNSリンク

`assets/profile.json` の `social` に各サービスの HTTPS URL を設定します。URLを変更したり、項目を削除または空にすると、次の公開時に全ページのフッターへ反映されます。表示対応サービスは `js/common.js` に定義し、ボタンの色は `index.css` で指定しています。リンクは新しいタブで開きます。JSONを取得できない場合、SNS欄のみを非表示にして他の表示は維持します。

## 記事へのコメント

各記事のコメント欄でタイトルとMarkdown本文を入力し、GitHubのIssueフォームを開いて確認・送信します。投稿にはGitHubへのログインが必要です。既存の `comment.yaml` に定義した `comment-for-post` ラベルを使い、公開記事のURLとコメントをフォームに事前入力します。以前の相対パスを記載したIssueも引き続き表示されます。ラベルは初回デプロイ時にワークフローが存在を確認し、なければ作成します。既存のラベルの色や説明は変更しません。URLの `labels` 指定は閲覧者の権限によってフォームが404になることがあるため使わず、フォーム自体の既定ラベルを使います。

Issueと追加コメントはGitHub上で公開されます。投稿や編集の後、Actionsが現在のIssue情報を取得して各記事を再生成するため、反映にはワークフローの実行時間がかかります。各Issueは独立したスレッドとして表示され、返信はGitHubのIssue上で行います。Issueの本文を編集する場合は、フォームが生成する「対象記事パス (Post Path)」「コメント内容 (Comment)」の見出しを残してください。見出しや対象記事が不明な場合は記事には表示されませんが、Close時にはIssueの内容をアーカイブします。公開コメントのMarkdownは安全なHTMLに変換されますが、すべてのGitHub固有機能が同じように表示されるわけではありません。

コメント者の通常のGitHubアカウント名はプロフィールへのリンクとして表示し、日時は日本標準時（JST）で表示します。削除済みアカウントなどプロフィールを確実に特定できない場合は、名前のみを表示します。アーカイブの元の日時は変更しません。

IssueをCloseすると `data/comments/archive/` の同じIssue番号のJSONにその時点の情報を保存します。ReOpen後は表示を更新してもアーカイブは変更せず、次のCloseで更新します。Actionsが待機している間にさらに編集や削除が行われた場合、GitHub APIから処理時に取得できる情報が保存されるため、Closeした瞬間の状態を完全には再現できません。コメント・アーカイブ・Gitの履歴は公開され、Issue上での削除や編集によって過去のGit履歴まで自動的に消えるわけではありません。個人情報の公開やスパムへの対応に注意してください。

Issueイベントの処理がブランチの同時更新や一時的な通信障害などで失敗すると、別の再試行ワークフローが元のイベントを使って最大2回再実行します。それでも失敗した場合は Actions の失敗ログを確認し、該当する実行を **Re-run failed jobs** で再実行してください。APIのレート制限に達した場合も復旧後の再実行が必要です。GitHubのキューと再試行には上限があり、すべてのイベントを無制限に保存できるものではありません。

## ローカルプレビュー

Docker はローカルプレビューに不要です。Python 3.12 と pip、Node.js 22 と npm、Git、curl、tar、および依存関係のインストールとe2d3のビルド時のネットワーク接続が必要です。`scripts/requirements.txt` の `cmarkgfm`・`github-slugger`・`nh3`・`garmin-fit-sdk` をインストールしてください。種別間リンクや索引の生成にも FIT の日時が必要です。

```sh
# リポジトリのルートで実行
python3 -m venv /tmp/greenlog-venv
/tmp/greenlog-venv/bin/pip install -r scripts/requirements.txt
npm ci
/tmp/greenlog-venv/bin/python scripts/build_site.py --site-dir /tmp/greenlog-site
python3 -m http.server --directory /tmp/greenlog-site 8000
```

ブラウザで `http://localhost:8000/` を開きます。`file://` では JSON 読み込みが制限されることがあるため、HTTP サーバーを使用してください。公開対象のルート `index.html`、各種別のテンプレート・CSS・JS が用意されている必要があります。

このローカル生成はGitHub APIに接続しないため、投稿フォームは表示されますが、Issueの既存コメントは表示されません。コメントを含むプレビューはGitHub Actions上で同期した公開結果を確認してください。

生成時は対象ディレクトリをまるごと消去して再生成します。誤消去防止のため、空でない既存ディレクトリは、以前の本スクリプトによる生成マーカー `.greenlog-build` がある場合のみ消去します。リポジトリやその親、`data/`、`scripts/`、`.github/` 等の内部は出力先に指定できません。

検証は次のコマンドで実行できます（テストは仮テンプレートと一時ディレクトリを使用）。

```sh
/tmp/greenlog-venv/bin/python scripts/test_build.py -v
/tmp/greenlog-venv/bin/python scripts/test_custom_embeds.py -v
/tmp/greenlog-venv/bin/python scripts/test_comments.py -v
node --test scripts/test_common.mjs
npm run test:e2d3
```

Nodeのテストは、公式ソースの場所を環境変数 `E2D3_CONTRIB_DIR` と `E2D3_RUNTIME_DIR` に設定した場合に統合テストも実行します。未設定ではその部分をスキップします。GitHub Actionsでは公式ソースを取得したうえで実行します。

## GitHub Pages の設定とデプロイ

1. リポジトリの **Settings → Pages → Build and deployment → Source** で **GitHub Actions** を選択します。Pages の利用条件・権限が満たされていることを確認してください。
2. リポジトリの `master` または `main` へ `data/sources/**`、画面や変換処理の変更を含むコミットを push するか、**Actions → Build and deploy GitHub Pages → Run workflow** を実行します。必要に応じて `github-pages` environment の保護ルールや承認を設定します。
3. ワークフローは checkout 後、Python・Nodeの依存をインストールして `.site` をクリーン作成し、`./.github/actions/blog`、`./.github/actions/training`、`./.github/actions/sample` の Docker Action で記事を生成します。続いてe2d3用の静的ファイルと `index.json` を生成し、GitHubのIssueとコメントを記事HTMLに反映します。生成結果の `data/contents/`、ルート `index.json`、必要なコメントアーカイブを対象ブランチに保存してから、`upload-pages-artifact` と `deploy-pages` で公開します。記事や媒体を削除した場合は次の公開結果にも反映します。差分がなければコミットや push はしません。
4. 初回公開後、Actions の実行結果と Settings → Pages に表示される URL で確認します。独自ドメインを使う場合は、リポジトリのルートに置いた `CNAME` が公開ディレクトリにコピーされます。

保存には Actions の `GITHUB_TOKEN` に対する `contents: write` 権限、コメントラベルの作成には `issues: write` 権限、失敗したIssue同期の自動再試行には `actions: write` 権限、および対象ブランチへ通常の push ができる許可が必要です。既存のブランチ保護や pre-push フックは迂回しません。権限・保護ルールによる拒否、またはビルド中のブランチ変更があった場合は失敗し、公開しません。設定の変更や保護の解除は自動では行いません。公開直前のブランチ確認後にも別の push が発生し得るため、この確認と Pages デプロイは原子的ではありません。

Docker Action は GitHub ホストの Linux ランナーで実行されます。独自ランナーでは Docker が必要です。ローカルでは上述の Python スクリプトで同じ変換を実行でき、Docker を使う必要はありません。ブログコンテナの依存バージョンは `scripts/requirements.txt` と一致させてください。

FIT と変換後の JSON には、位置情報、心拍などの個人情報が含まれることがあります。公開リポジトリや GitHub Pages に保存すると閲覧可能になるため、公開前に内容を確認してください。
