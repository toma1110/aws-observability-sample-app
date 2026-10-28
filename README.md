# 観測練習用の注文受付アプリ

この小さなアプリは、成功・入力エラー・処理エラー・短い意図的な遅延を実際に起こし、Lambdaの実行ログを読む練習に使います。HTTP endpointはIAM認証必須です。匿名の利用者は呼び出せず、APIの本文に個人情報や秘密情報を入れないでください。

## 作成するものと費用

CloudFormation stackはLambda関数、IAM実行role、3日保持のCloudWatch Logs log group、IAM認証付きLambda Function URLを作ります。データベース、外部接続、顧客データ保存はありません。Function URLはHTTP API Gatewayを経由しません。

Lambdaの呼び出し回数・実行時間とCloudWatch Logsの取り込み/保存には利用量に応じた料金が発生する場合があります。Function URL自体にはendpoint追加料金はありません。料金はRegion、無料利用枠、利用量、ログ量などで変わるため、この演習でも固定金額は保証できません。実行前に[AWS Lambda料金](https://aws.amazon.com/lambda/pricing/)と[CloudWatch料金](https://aws.amazon.com/cloudwatch/pricing/)で東京Regionの料金を確認し、必要ならAWS Pricing Calculatorで演習回数と保持量を仮定して見積もってください。Cost Explorerで利用後の実額を確認します。連続実行や大量ログ出力は避け、演習終了後すぐstackを削除してください。

## 必要な権限と前提

AWS CloudShellを使います。AWS CLI v2、Python 3、boto3/botocoreが必要です（CloudShellに含まれます）。アカウント所有者の管理権限で普段の演習をしないでください。CloudFormation stackの作成/更新/削除、Lambda関数とFunction URLの作成/削除、IAM role作成と`iam:PassRole`、CloudWatch Logs log group作成/書き込み/参照、AWS STS caller identity確認が必要です。関数URLを呼び出す利用者identityには、対象関数ARNへの`lambda:InvokeFunctionUrl`と`lambda:InvokeFunction`の両方が必要です。Function URL自体にはresource policyを付けず、同一アカウントの利用者側identity policyで許可します。詳しくは[AWSのFunction URL認証仕様](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)を確認してください。IAM権限境界や組織ポリシーで許可されない場合は管理者へ相談し、制限を回避しないでください。

CloudShellのRegionを東京（`ap-northeast-1`）に設定します。受講者教材では固定tag `v1.0.0` を取得します。mainの最新内容をそのまま使わず、Course教材にもこのtagとREADME URLを記録してください。

```bash
export AWS_REGION=ap-northeast-1
git clone --branch v1.0.0 --depth 1 https://github.com/toma1110/aws-observability-sample-app.git
cd aws-observability-sample-app
aws sts get-caller-identity
aws configure get region
```

表示されたアカウントとRegionが自分の演習対象であることを確認します。違っていたら先へ進まずCloudShell設定を直してください。stack名は英字から始まる3〜31文字の値にし、複数の受講者で共有するアカウントでは自分固有のsuffixを付けます。deploy/cleanupとも`STACK_NAME`を必須とし、stack所有tagが一致しない既存stackは変更・削除しません。

## デプロイ

```bash
export STACK_NAME=observability-sample-learner1
./deploy.sh
```

出力されたFunctionUrlを環境変数に保存します。呼び出しidentityには対象関数ARNへの`lambda:InvokeFunctionUrl`と`lambda:InvokeFunction`の両方が必要です。二つ目はFunction URL経由だけに絞る例です。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":"lambda:InvokeFunctionUrl","Resource":"arn:aws:lambda:ap-northeast-1:ACCOUNT_ID:function:STACK_NAME"},
    {"Effect":"Allow","Action":"lambda:InvokeFunction","Resource":"arn:aws:lambda:ap-northeast-1:ACCOUNT_ID:function:STACK_NAME","Condition":{"Bool":{"lambda:InvokedViaFunctionUrl":"true"}}}
  ]
}
```

```bash
export FUNCTION_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text)"
printf '%s\n' "$FUNCTION_URL"
```

deployは`app.py`をCloudFormationの`Code.ZipFile`へ埋め込んだtemplateを生成してstackを作ります。Function URLはAWS_IAM認証で、URLを知っているだけでは呼び出せません。認証には現在のCloudShell identityが使われます。

## 検証した動作

2026-10-28に東京Region（`ap-northeast-1`）で検証しました。LambdaはPython 3.12、128 MB、timeout 8秒、Function URLは`AWS_IAM`認証です。正常注文、quantity 0の入力エラー、教材用のfailure注入、1.5秒delayを各1回実行し、HTTP statusは順に`200`、`400`、`500`、`200`でした。アプリがPython `perf_counter`で記録したhandler処理時間（`durationMs`）は約`0.10 ms`、`0.08 ms`、`0.08 ms`、`1500.29 ms`でした。この値はLambdaの課金対象時間や`REPORT`行のdurationとは異なります。各レスポンスとCloudWatch Logsの`request_complete` eventは、同じ`requestId`で4件すべて照合しました。採取した同一runの証跡file SHA-256は`0c46e062be9191d0a31752a8fffa03af49fda5056e8f63d61f2941de14af6e97`です。これは一回の検証結果であり、実行環境によってdurationは変わります。

## 操作して実ログを採取

次のscriptは正常注文、quantity 0の入力エラー、教材用の故障注入、1.5秒の上限内sleepを各1回実行します。各HTTP responseのrequestIdでCloudWatch Logsから該当するアプリJSON logを検索し、対にして表示します。CloudWatch Logsの読み取り権限も必要です。

```bash
python3 run_scenarios.py "$FUNCTION_URL" | tee run-evidence.txt
```

期待されるHTTP statusは順に`200`、`400`、`500`、`200`です。正常注文は`SKU-RED-01`を数量2で受け付け、合計1000を返します。quantity 0は実際の入力検証で`validation_error`になります。failureだけが説明用故障注入で`internal_error`を返し、内部の例外文字列はレスポンスへ出しません。delayは注文処理の前に最大3000msの`time.sleep`を行います。1.5秒指定時の`durationMs`は通常処理より約1500ms長くなりますが、起動時間等も加わります。

Function名とlog group名はstack名と同じです。上のscriptはHTTP操作と同一runのアプリJSON logを表示してファイルに保存します。Lambdaの`START`/`END`/`REPORT`行はアプリJSON eventとは別のplatform logです。scriptはrequestIdで該当する`event=request_complete`のアプリJSONを照合します。

```bash
python3 invoke.py "$FUNCTION_URL" '{"sku":"SKU-BLUE-02","quantity":1}'
aws logs tail "/aws/lambda/$STACK_NAME" --since 10m | tee recent-lambda-logs.txt
```

各requestには次のような構造化イベントが出ます。次は形式を説明する例で、実行結果ではありません。教材では上の手順で採取した`run-evidence.txt`内の実HTTP出力と同じrunのlogを使い、合成例を実ログとして扱わないでください。

```json
{"event":"request_complete","requestId":"Lambdaが割り当てたID","status":200,"durationMs":12.34,"error":null}
```

`status`、`durationMs`、`error`を比較します。失敗ケースでerrorに出るのは例外型だけで、入力内容やsecretは記録しません。期待値と違う場合は`aws cloudformation describe-stacks`でstack状態、`aws lambda get-function-url-config`でURL認証方式、CloudWatch Logsで同じrequestIdの実行を確認します。認証エラー時はCloudShellのidentityとRegionを確認し、URLを公開認証へ変えないでください。

## 削除と残存確認

演習後はすぐ削除します。CloudFormationが管理するLambda、Function URL、IAM role、log groupをstackごと削除します。

```bash
./cleanup.sh
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$STACK_NAME"
```

`cleanup.sh`はstack削除完了後、stack、Lambda関数、Function URL、IAM role、完全一致するlog groupが存在しないことをAPIで確認します。AccessDeniedなどのエラーは「存在しない」と扱わず失敗として停止します。削除失敗時はCloudFormationのEventsで失敗resourceを調べ、原因を修正して同じstackを削除し直します。手動削除で別のstackやresourceを触らないでください。ログを保存した場合は必要な学習記録を残した後、自分のCloudShellファイルからも削除します。

## Courseで使う場合

既存Courseでアプリのclone、実装、デプロイをCourse内に置いていた場合は、次の形へ移行します。

1. Course独自アプリの再実装を止め、このREADMEの固定tag `v1.0.0`と実行・IAM・費用・cleanup手順へ置き換えます。
2. 受講者に正常、入力エラー、故障注入、delayを実行してもらい、各操作のrequest input・HTTP response・同一`requestId`の実ログを採取します。
3. 共通アプリとAWS構築の実装はこのrepositoryで管理し、Course固有のPoC、比較、判断やログ解釈をCourse側で進めます。実操作から採取したrequest input、HTTP response、`requestId`、対応するCloudWatch logをセットで教材に引用・保存できます。合成例を実ログとして扱わず、個人情報、アカウントID、credentialは含めないでください。

Courseの手順には実行環境、必要権限、Region、対象操作、期待するログ項目、cleanup完了を明記してください。Course専用のAI支援PoCや比較評価は各Course側の責務です。

## 動作確認

```bash
python3 -m unittest -v
```
