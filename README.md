# ログの読み方を学ぶ注文受付アプリ

注文を送信し、その結果をAWS Lambdaの実行ログで確かめる演習です。
正常な注文に加えて、入力エラー、意図的な処理エラー、遅い処理を試せます。

この演習では、次のことを確認します。

- 送信した注文と、アプリから返った応答を対応付ける。
- 同じ処理のログを見つけ、成功・失敗・処理時間を読み取る。
- 演習で作成したAWSリソースを削除し、残っていないことを確かめる。

## 1. 構成と料金を確認する

### 作成するもの

AWS CloudFormationを使い、演習用リソースをまとめて作成します。
このまとまりを「スタック」と呼びます。

| リソース | この演習での役割 |
| --- | --- |
| Lambda関数 | 注文を受け付けるPythonプログラムを実行します。Python 3.12、メモリ128 MB、タイムアウト8秒で動作します。 |
| Lambda Function URL | 注文を送信するHTTPの窓口です。IAM認証を使います。 |
| IAM実行ロール | Lambdaが自分のログを書き込むための権限です。 |
| CloudWatch Logsのロググループ | 実行ログの保存先です。ログは3日間保持します。 |

注文はサンプルの商品情報だけで処理し、データベースには保存しません。
Function URLはAPI Gatewayを経由せず、Lambdaを直接呼び出します。

IAM認証には、AWSで操作中のユーザーやロールの権限を使います。
URLを知っているだけの匿名ユーザーは呼び出せません。
送信する注文には、個人情報やパスワードなどの秘密情報を含めないでください。

### 料金が発生するもの

Lambdaの呼び出し回数・実行時間と、CloudWatch Logsへのログの取り込み・保存が課金対象になります。
無料利用枠などによって実際の支払額は変わります。
Function URL自体の追加料金はありません。

実行前に、東京リージョンの料金を確認してください。

- [AWS Lambdaの料金](https://aws.amazon.com/lambda/pricing/)
- [Amazon CloudWatchの料金](https://aws.amazon.com/cloudwatch/pricing/)

必要に応じて、AWS Pricing Calculatorで実行回数やログ量を仮定して見積もります。
利用後の実額はCost Explorerで確認します。
連続実行や大量のログ出力を避け、演習後は「7. 後片付け」を実行してください。

## 2. 実行環境と権限を準備する

### 実行環境

AWSマネジメントコンソールで、東京リージョン（`ap-northeast-1`）を選びます。
続いてAWS CloudShellを開いてください。
CloudShellは、ブラウザからコマンドを実行できる環境です。

この演習では、CloudShellに含まれるAWS CLI v2、Python 3、boto3/botocoreを使います。
boto3/botocoreは、PythonからAWSを操作し、リクエストを認証するためのライブラリです。

### 必要な権限

演習用のユーザーまたはロールを使ってください。
アカウント所有者の管理権限を、普段の演習用に使うことは避けます。

次の操作を行える必要があります。

- CloudFormationスタックの作成・更新・削除と、状態・出力の参照。変更セットの作成・確認・実行・削除も必要です。
- Lambda関数とFunction URLの作成・削除・設定参照
- IAMロールの作成・削除・参照と、Lambdaへロールを渡す`iam:PassRole`
- CloudWatch Logsのロググループの作成・削除・参照と、ログの書き込み・読み取り
- AWS STSによる、操作中のアカウントとユーザー・ロールの確認

変更セットは、スタックに加える変更をCloudFormationがまとめたものです。`deploy.sh`の内部で使います。

Function URLの呼び出しには、さらに対象関数への次の2つの権限が必要です。

- `lambda:InvokeFunctionUrl`
- `lambda:InvokeFunction`

このアプリでは、同じAWSアカウント内のユーザーやロールに権限を付けて呼び出します。
Function URL側にリソースポリシーは追加しません。
詳しくは[AWSのFunction URL認証仕様](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)を参照してください。

組織のポリシーなどで操作が許可されない場合は、管理者に相談してください。
必要な権限を確認してから先へ進みます。

## 3. アプリを取得する

以下のコマンドをCloudShellで実行します。
`v1.0.2`は、この手順で使うアプリの版を指定するタグです。

```bash
export AWS_REGION=ap-northeast-1
git clone --branch v1.0.2 --depth 1 https://github.com/toma1110/aws-observability-sample-app.git
cd aws-observability-sample-app
aws sts get-caller-identity
printf '%s\n' "$AWS_REGION"
```

`get-caller-identity`の`Account`と`Arn`で、演習用のアカウントとユーザー・ロールを確認します。
最後のコマンドでは、この演習で使う環境変数`AWS_REGION`の値を表示します。
この手順のコマンドは、環境変数`AWS_REGION`に指定した東京リージョンで実行します。

アカウントやリージョンが演習対象と違う場合は、設定を確認してから進めてください。

## 4. AWSにデプロイする

デプロイは、プログラムをAWSへ配置して実行できる状態にする操作です。
まず、作成するスタックに名前を付けます。

`learner1`は自分用の文字列へ置き換えてください。
同じアカウントを複数人で使う場合は、名前が重ならないようにします。
名前は英字で始まる3〜31文字で、英数字とハイフンを使えます。

```bash
export STACK_NAME=observability-sample-learner1
./deploy.sh
```

完了すると、関数名やFunction URLなどが表示されます。
`deploy.sh`はアプリをCloudFormationのテンプレートに組み込み、スタックを作成します。
既存スタックがある場合は、このサンプルの所有タグが一致するものだけを更新します。

### 呼び出し権限を確認する

次は、操作中のユーザーやロールに付ける呼び出し権限の例です。
`ACCOUNT_ID`を自分のAWSアカウントID、`STACK_NAME`を先ほどのスタック名に置き換えます。
関数名はスタック名と同じです。

権限が不足する場合は、管理者にこの例を示して設定を依頼してください。
2つ目の権限は、Function URL経由の呼び出しに限定しています。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunctionUrl",
      "Resource": "arn:aws:lambda:ap-northeast-1:ACCOUNT_ID:function:STACK_NAME"
    },
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:ap-northeast-1:ACCOUNT_ID:function:STACK_NAME",
      "Condition": {
        "Bool": {"lambda:InvokedViaFunctionUrl": "true"}
      }
    }
  ]
}
```

### 呼び出し先を保存する

次のコマンドで、スタックの出力からFunction URLを取得します。
`FUNCTION_URL`は、この後のコマンドで使うための環境変数です。

```bash
export FUNCTION_URL="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" --output text)"
printf '%s\n' "$FUNCTION_URL"
```

`https://`で始まるFunction URLが表示されることを確認してください。
この後も同じCloudShellターミナルで操作し、`AWS_REGION`、`STACK_NAME`、`FUNCTION_URL`を保持します。
認証には、CloudShellで操作中のユーザーやロールが使われます。

## 5. 4種類の注文を実行する

次のコマンドは4種類の注文を各1回送信します。
入力内容、HTTP応答、対応するCloudWatch Logsのログを順に表示します。
`tee`は、画面の出力を`run-evidence.txt`にも保存するコマンドです。

```bash
python3 run_scenarios.py "$FUNCTION_URL" | tee run-evidence.txt
```

### 期待する結果

HTTPステータスは、処理結果を表す番号です。
この演習では、成功の`200`、入力エラーの`400`、処理エラーの`500`を比較します。

| 表示される操作名 | 試すこと | HTTP | 応答で確認する内容 |
| --- | --- | --- | --- |
| `success` | 赤いノートを2冊注文する | `200` | `quantity`が2、`total`が1000 |
| `validation` | 数量を0にして注文する | `400` | `error`が`validation_error` |
| `injected_failure` | 意図的に処理エラーを起こす | `500` | `error`が`internal_error` |
| `bounded_delay` | 1.5秒待って青いペンを2本注文する | `200` | `delayMs`が1500、`total`が500 |

商品コード`sku`は、赤いノートが`SKU-RED-01`、青いペンが`SKU-BLUE-02`です。
数量を表す`quantity`には、1〜10の整数を指定します。

`injected_failure`の`500`は、エラーの調査練習のためにアプリが返す応答です。
Lambdaサービス自体の障害を示すものではありません。
`bounded_delay`は、注文処理の前に意図的な待ち時間を入れます。
アプリが受け付ける待ち時間の上限は3000ミリ秒です。

## 6. 応答とログを読み比べる

### 同じ処理のログを見つける

`requestId`は、Lambdaがリクエストごとに割り当てるIDです。
HTTP応答とログで同じIDを探すと、どの注文の記録かを確認できます。

各操作では、次の順に出力されます。

1. `Request input`：送信した注文
2. `HTTP response`：アプリから返った応答
3. `Matching CloudWatch app JSON log`：その応答と同じ`requestId`を持つログ

スクリプトは、処理完了を表す`request_complete`のログを検索します。
ログが届くまで待つため、各操作の表示に時間がかかることがあります。

### ログの項目を確認する

次は、ログの形式を説明するための例です。実行結果ではありません。
自分の結果は、画面または`run-evidence.txt`で確認してください。

```json
{
  "event": "request_complete",
  "requestId": "Lambdaが割り当てたID",
  "status": 200,
  "durationMs": 12.34,
  "error": null
}
```

| 項目 | 読み取ること |
| --- | --- |
| `event` | `request_complete`は、アプリの処理完了を表します。 |
| `requestId` | HTTP応答と同じIDかを確かめます。 |
| `status` | HTTPステータスです。成功・入力エラー・処理エラーを区別します。 |
| `durationMs` | アプリ内で計測した処理時間です。単位はミリ秒です。 |
| `error` | 成功時は`null`、入力エラーは`validation`、意図的な処理エラーは`RuntimeError`になります。 |

通常の注文と遅延ありの注文で、`durationMs`を比較してください。
遅延ありの処理には、約1500ミリ秒の待ち時間が含まれます。
数値は実行ごとに変わります。

`durationMs`には、入力検証などのアプリの処理を含みます。
Lambdaの初期化時間は含まず、課金対象時間や`REPORT`行の処理時間とも異なります。
エラー時のログにはエラーの種類を記録し、注文本文や秘密情報は記録しません。

### 自分で注文を1件送る場合

次は、青いペンを1本注文する例です。
続くコマンドで、直近10分間のログを表示・保存します。

```bash
python3 invoke.py "$FUNCTION_URL" '{"sku":"SKU-BLUE-02","quantity":1}'
aws logs tail "/aws/lambda/$STACK_NAME" --since 10m | tee recent-lambda-logs.txt
```

ロググループ名は`/aws/lambda/<スタック名>`です。
直近のログには複数の注文が含まれるため、応答の`requestId`で対象を探してください。
Lambdaが出力する`START`・`END`・`REPORT`行も表示されます。
これらは、アプリのJSON形式のログとは別の実行記録です。

### 期待した結果にならない場合

| 状況 | 確認すること |
| --- | --- |
| デプロイに失敗した | CloudFormationのスタックと「イベント」で、失敗したリソースと理由を確認します。 |
| 認証エラーで呼び出せない | CloudShellのアカウント・ユーザーやロール、東京リージョンの設定、2つの呼び出し権限を確認します。Function URLの認証方式は`AWS_IAM`のままにします。 |
| 対応するログが見つからない | `STACK_NAME`、ログの読み取り権限、同じ`requestId`のログが届いているかを確認します。 |
| `URL does not match`と表示された | `FUNCTION_URL`が、`STACK_NAME`で指定したスタックの出力と一致するか確認します。 |

失敗時は、原因を確認してから再実行してください。
エラーのまま繰り返し実行すると、呼び出し回数やログ量が増えます。

## 7. 後片付け

ログを後の学習に使う場合は、必要なファイルを先に保存してください。
スタックを削除すると、CloudWatch Logsに保存したログも削除されます。
CloudShellの`run-evidence.txt`などのファイルは残ります。

同じCloudShellターミナルで、演習用のスタック名を確認してから削除します。

```bash
printf '%s\n' "$AWS_REGION" "$STACK_NAME"
./cleanup.sh
```

`cleanup.sh`は、スタックと、その中のLambda関数・Function URL・IAMロール・ロググループを削除します。
さらに、それぞれが残っていないことをAPIで確認します。
削除と確認が完了すると、次のメッセージが表示されます。

```text
Deleted stack and confirmed exact Lambda function, Function URL, IAM role, and log group are absent.
```

これは、演習用リソースの削除と残存確認が完了したという意味です。
`AccessDenied`などのエラーで停止した場合は、削除完了として扱わないでください。

削除に失敗した場合は、CloudFormationの「イベント」で理由を調べます。
原因を解消した後、同じスタックを削除し直してください。
別のスタックやリソースを削除しないよう、対象名を確認します。

必要に応じて、次のコマンドでも状態を確認できます。

```bash
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$STACK_NAME"
```

スタック一覧には、同じアカウントの別のスタックが表示されることがあります。
演習用の名前がないことと、ロググループ一覧に完全一致する名前がないことを確認してください。

CloudShellに保存したログも、学習で使い終えたら削除してください。
