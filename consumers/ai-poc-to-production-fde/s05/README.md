# ログを人とAIで調べる

共有注文アプリの同じ実行ログを、まず自分で調べ、その後にAmazon Bedrockの回答と比べます。AIには一度のログ検索だけを許し、今回の実行記録にあるJSONログ1件だけを返します。ログにない原因や影響は、分からないまま区別します。

AIの回答は調査の補助です。運用上の判断や障害原因の確定には使いません。

## 始める前に

共有アプリの[固定版README](https://github.com/toma1110/aws-observability-sample-app/blob/v1.0.2/README.md)を先頭から読み、そこに書かれた前提確認、AWSへの配置、注文の実行、後片付けを行います。このREADMEは共有アプリの手順を置き換えません。

このconsumerは、共有アプリ`v1.0.2`のtagには含まれません。Courseで案内されたconsumerのexact commitから、`consumers/ai-poc-to-production-fde/s05/`を取得して使います。アプリを実行する前に、Courseに示されたconsumerのcommitとこのREADMEの版を確認してください。

Courseに表示された40桁のconsumer commitを使って、共有アプリの固定tagを保ったままconsumerだけを取得します。`CONSUMER_COMMIT`はCourseに記載された値へ置き換えてください。`main`は取得元の履歴を得るためだけにfetchし、consumerの内容は必ずCourseに表示されたcommitから取り出します。

```bash
git clone https://github.com/toma1110/aws-observability-sample-app.git
cd aws-observability-sample-app
git checkout --detach v1.0.2
CONSUMER_COMMIT='Courseに表示された40桁のconsumer commit'
git fetch origin main
git cat-file -e "${CONSUMER_COMMIT}^{commit}"
git archive "$CONSUMER_COMMIT" consumers/ai-poc-to-production-fde/s05 | tar -x -C .
test -f consumers/ai-poc-to-production-fde/s05/investigate.py
git rev-parse HEAD
git rev-parse "$CONSUMER_COMMIT"
```

最後の2行は順に`v1.0.2`のapp commitとCourse記載のconsumer commitを表示します。Courseに記載されたcommitと一致しない場合は作業を止めて確認してください。

BedrockでAI調査を行う場合は、AWSアカウントで`amazon.nova-lite-v1:0`への推論が許可され、`ap-northeast-1`のBedrock Runtimeを呼べる必要があります。権限がない場合は、自分で権限を広げず、管理者に確認してください。AI調査をしない場合も、先にログを人が読んで記録するところまでは試せます。

推論は最大350出力トークンです。このconsumer folderでのローカル実行は、異なるrequest IDを使うmain調査4件まで（各2回、合計8回）と、syntheticなsmoke test 1回（合計2回）に分かれ、合計10回を上限にします。request IDごとの2回目は同じtool resultを使った最終回答です。一度使ったrequest IDやsmoke枠は再実行できません。APIエラーや想定と異なるtool要求が返った場合も、その回数は消費され、自動再試行しません。上限に達した場合やエラーが出た場合は追加実行を止めて状態を確認してください。`api-usage.json`を削除・初期化して上限を戻さないでください。

## 実行結果を準備する

固定版アプリを実行した同じCloudShellで、共有アプリのリポジトリ直下に移動します。次のファイルがあることを確認してください。

```text
run-evidence.txt
consumers/ai-poc-to-production-fde/s05/
```

`run-evidence.txt`は、共有アプリの`run_scenarios.py`が今回実際に出力したものを使います。別の実行のログを混ぜたり、JSON例を実行記録として作ったりしないでください。

## 先に自分で調べる

consumer directoryへ移動します。

```bash
cd consumers/ai-poc-to-production-fde/s05
```

`run-evidence.txt`からJSONログを一つ選び、表示された`requestId`を使ってconsumer directoryで次を実行します。

```bash
export REQUEST_ID='run-evidence.txtに表示されたrequestId'
python3 investigate.py --show-log --request-id "$REQUEST_ID"
```

`REQUEST_ID`には今回の`run-evidence.txt`に表示された実際の値を入れてください。画面にはそのIDの`request_complete` JSONログ1件だけが表示されます。HTTP応答や注文入力を根拠に加えず、そのJSONに記録された事実と、ログだけでは分からないことを先に記録します。アプリを実行する時点では操作名や入力条件を知るため、完全に情報を伏せた比較ではありません。その前提を記録し、原因についての判断に入力条件など他の情報を使った場合は「追加で確認した証拠」として分けてください。

人の調査にかかった時間は、調べ始める前と終わった後に、実際の時刻とタイムゾーンを記録してください。たとえば`date --iso-8601=seconds`はCloudShellの時刻とタイムゾーンを表示します。時刻や結論を後から推測して埋めないでください。

テンプレートをコピーして編集します。

```bash
cp human-investigation.template.json human-investigation.json
nano human-investigation.json
```

`request_id`と`evidence_request_ids`には表示された実際の`requestId`を入れ、`query`にはそのJSONだけに答える問いを書きます。`started_at`と`completed_at`にはタイムゾーン付きの実時刻を入れ、`findings`と`uncertainty`には自分の調査結果と残る不明点を書きます。Bedrockを呼ぶ前にこの記録を保存してください。人の調査内容やシナリオ名はBedrockへ送信されず、AI toolには同じJSONログ1件と同じ問いだけが渡ります。

### 講師用の想定例

Course ownerの指示で教材用に想定例を使う場合は、実際の人の調査結果として扱わず、調査時間も記録しません。次の例はログ1件と問いを固定したまま、由来を`hypothetical`として明示します。consumerはこのmodeでIssueの固定問いとの完全一致を確認し、問いを変えた例を拒否します。受講者の`actual` modeでは自分が調べる問いを記録できます。

```bash
cp hypothetical-investigation.example.json human-investigation.json
python3 investigate.py --validate-only
```

例の`started_at`/`completed_at`は`null`です。架空の日時や実測時間欄を作らず、想定所要時間は`illustrative_duration`へ「教材用の想定」と明記して分けます。例では「3分程度」としています。結果JSONには`baseline_provenance: hypothetical`と`human_investigation_duration_ms: null`が残ります。想定例の所見と想定時間はAIへ送信せず、AIには通常modeと同じ問いと対象JSONログ1件だけを渡します。実測modeではテンプレートの`provenance: actual`を維持し、実際の開始・終了時刻を記録します。想定時間と実測値から短縮率を出すことはできません。

記録が現在の実行ログと対応するかを確認します。このコマンドはBedrockを呼びません。

```bash
python3 investigate.py --validate-only
```

`requestId`が違う場合は停止し、人の記録と実行ログを確認してください。

## AIの調査を行う

人の記録を保存し、対応する実行ログを確認した後に限り、次を実行します。

```bash
python3 investigate.py
```

AIは必須の`search_run_logs` toolを一度呼び、選んだ`requestId`のログだけを受け取ります。ファイルを開いたりAWSを操作したりするtoolはありません。AIが別のtoolや別の入力を要求したときは停止します。

回答は`ai-investigation-<requestId>.json`に保存されます。このファイルには、人の記録、対象ログ、AI回答、各Converse応答の利用量、API応答時間が含まれます。API応答時間は人が回答を確認・修正した時間ではありません。

AIの回答を確認するときは、実際に確認を始めた時刻と終えた時刻を記録し、回答を残した点・修正した点、追加で見た証拠、不確実なまま残った点を記入します。

```bash
cp ai-review.template.json ai-review.json
nano ai-review.json
```

この確認記録と結果JSONは作業用です。公開前に残さず、必要な比較情報を講義用の成果物へ転記する場合も、実ログや実アカウント情報を公開しないでください。

所要時間は次の3つを分けて記録します。実測modeの人の調査時間は`human-investigation.json`の開始・完了時刻、BedrockのAPI応答時間は`ai-investigation-*.json`、AI回答を人が確認・修正した時間は`ai-review.json`の開始・完了時刻から得ます。想定modeには人の調査時間がありません。API応答時間だけを人の作業時間や短縮時間と読み替えず、想定例と実測値から短縮率を出しません。必要なら「AI支援の経過時間」をAPI応答時間と人の確認時間の合計として併記できますが、どの区間を足したか明記し、準備時間を含まない経過時間として扱います。

## Converseのtool-useだけを試す場合

人の比較記録や共有アプリの実行ログを使わず、syntheticなログでtool-useを一度だけ確認するには、次を使います。

```bash
python3 investigate.py --smoke-test-only
```

このモードは`human-investigation.json`と`run-evidence.txt`を読みません。2回のConverse呼出しだけを使い、結果を`smoke-test-only.json`へ分けて保存します。これは動作確認用の架空データで、共有アプリの実行結果や人とAIの比較データには含めません。smoke testはこのconsumer folderで1回だけ実行できます。実行後は同じローカル呼出し予算を消費するため、`api-usage.json`の残りを確認してください。

## うまくいかない場合

- `No recognized completed app JSON logs`が出たら、共有アプリが今回出力した`run-evidence.txt`を確認します。
- 選んだログが見つからない場合は、別runの値で補わず、記録したrunとrequest IDを確認します。
- AWSの権限エラー、model IDエラー、tool要求の不一致が出たら、繰り返し実行せずエラーと利用量ファイルを確認します。
- main枠8回、smoke枠2回、request IDごとの2回、またはsmokeの1回制限に達した場合は停止します。`api-usage.json`を消して上限を戻さないでください。

## 後片付け

このconsumerのログ調査とBedrock呼出しは追加の永続AWSリソースを作成しません。Bedrock Runtimeの呼出し料金は発生します。共有アプリのLambdaとCloudWatch Logsは共有アプリのstackが存在する間残るため、別料金が発生します。演習後は共有アプリREADMEの手順でstackを削除し、CloudWatch Logsや実行中のリソースが残っていないことを確認してください。

CloudShell内の結果を必要な学習記録へ安全に保存した後、`human-investigation.json`、`ai-review.json`、`ai-investigation-*.json`、`smoke-test-only.json`、`api-usage.json`、`run-evidence.txt`など、不要になった実行ファイルを削除します。実行ログはAWSリソースを削除しただけではCloudShellから消えません。
