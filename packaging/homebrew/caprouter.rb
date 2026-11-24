class Caprouter < Formula
  include Language::Python::Virtualenv

  desc "Local capability ledger and MCP bus for AI-agent skills"
  homepage "https://github.com/<owner>/caprouter"
  url "https://files.pythonhosted.org/packages/<path>/caprouter-0.7.0.tar.gz"
  sha256 "<sha256-of-sdist>"
  license "MIT"

  depends_on "python@3.11"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "caprouter 0.7.0", shell_output("#{bin}/caprouter --version")
  end
end
