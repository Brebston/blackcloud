require ["fileinto"];
# Rspamd додає "X-Spam: Yes" для дії add_header
if header :is "X-Spam" "Yes" {
  fileinto "Junk";
  stop;
}
