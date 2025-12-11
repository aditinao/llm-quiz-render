# solver/engine.py
if form and form.get('action'):
submit_url = form.get('action')
else:
# look for a JS object with submit
for s in soup.find_all('script'):
text = s.string or s.text or ''
if 'submit' in text and 'url' in text:
# naive attempt to find a quoted URL
import re
m = re.search(r'https?://[^\'\"\s]+/[^\'\"\s]*', text)
if m:
submit_url = m.group(0)
break


if not submit_url:
# fallback: if the server provided a static hint
submit_url = current_url.rstrip('/') + '/submit'


logger.info('Submitting to %s payload keys=%s', submit_url, list(payload.keys()))


try:
resp = post_json(submit_url, payload)
except requests.HTTPError as e:
# if server requires multipart (file) or special headers we attempt fallback
logger.exception('POST JSON failed, trying form-data fallback')
# If answer is an image (figure) demonstrate upload
try:
fig = plt.figure()
plt.text(0.5, 0.5, str(answer), fontsize=12, ha='center')
png = make_png_bytes(fig)
filename = 'answer.png'
# file_tuple (fieldname, (filename, bytes, content_type))
file_tuple = (filename, png, 'image/png')
resp = post_file(submit_url, {'email': email, 'secret': secret}, file_tuple)
except Exception as ex:
logger.exception('Fallback file upload failed')
raise


logger.info('Submit response: %s', resp)


# Inspect response to see if a next url is provided
next_url = None
if isinstance(resp, dict):
next_url = resp.get('url')
correct = resp.get('correct')
if correct:
logger.info('Answer correct, got next url: %s', next_url)
else:
logger.info('Answer incorrect: %s', resp.get('reason'))
# tests allow re-submit within 3 minutes; optionally choose to re-submit same data or continue


# move to next URL or stop
if not next_url:
return {'last_response': resp}
current_url = next_url


raise RuntimeError('Timeout or no next URL')
